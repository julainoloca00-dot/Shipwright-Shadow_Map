// Corrective world-shadow policy for Navi.
//
// Navi may still contribute to the normal toon-light selection, but she must never change the global
// directional shadow-map light, create a radial shadow/fill halo, or submit fairy geometry as a caster.
// This file sorts after ToonLighting.cpp and therefore applies the final world-shadow state each frame
// and the final caster state immediately before Navi draws.

#include <algorithm>
#include <cmath>
#include <memory>

#include <fast/Fast3dWindow.h>
#include <fast/interpreter.h>
#include <ship/Context.h>

#include "soh/Enhancements/game-interactor/GameInteractor.h"
#include "soh/ShipInit.hpp"
#include "soh/cvar_prefixes.h"

extern "C" {
#include "functions.h"
#include "macros.h"
#include "variables.h"
#include "z64.h"
#include "overlays/actors/ovl_En_Elf/z_en_elf.h"

extern PlayState* gPlayState;
}

namespace {

std::shared_ptr<Fast::Interpreter> GetShadowInterpreter() {
    auto window = std::dynamic_pointer_cast<Fast::Fast3dWindow>(Ship::Context::GetInstance()->GetWindow());
    if (window == nullptr) {
        return nullptr;
    }
    return window->GetInterpreterWeak().lock();
}

void NormalizeOrDefault(float direction[3]) {
    const float lengthSquared = direction[0] * direction[0] + direction[1] * direction[1] +
                                direction[2] * direction[2];
    if (lengthSquared <= 0.000001f) {
        direction[0] = 0.30f;
        direction[1] = 1.00f;
        direction[2] = 0.20f;
        return;
    }

    const float inverseLength = 1.0f / std::sqrt(lengthSquared);
    direction[0] *= inverseLength;
    direction[1] *= inverseLength;
    direction[2] *= inverseLength;
}

void ReadEnvironmentDirection(PlayState* play, float direction[3]) {
    LightInfo* sun = &play->envCtx.dirLight1;
    LightInfo* moon = &play->envCtx.dirLight2;
    const int sunLuminance = sun->params.dir.color[0] + sun->params.dir.color[1] + sun->params.dir.color[2];
    const int moonLuminance = moon->params.dir.color[0] + moon->params.dir.color[1] + moon->params.dir.color[2];
    LightInfo* environment = moonLuminance > sunLuminance ? moon : sun;

    direction[0] = environment->params.dir.x;
    direction[1] = environment->params.dir.y;
    direction[2] = environment->params.dir.z;
    NormalizeOrDefault(direction);
}

void ApplyNaviShadowPolicy() {
    auto interpreter = GetShadowInterpreter();
    if (interpreter == nullptr ||
        CVarGetInteger(CVAR_ENHANCEMENT("Graphics.WorldShadows.Enabled"), 0) == 0) {
        return;
    }

    float shadowDirection[3] = { 0.30f, 1.00f, 0.20f };
    float shadowAnchor[3] = { 0.0f, 0.0f, 0.0f };

    if (gPlayState != nullptr) {
        ReadEnvironmentDirection(gPlayState, shadowDirection);

        Player* player = GET_PLAYER(gPlayState);
        if (player != nullptr) {
            shadowAnchor[0] = player->actor.world.pos.x;
            shadowAnchor[1] = player->actor.world.pos.y;
            shadowAnchor[2] = player->actor.world.pos.z;
        }
    }

    // A zero radius removes the local shadow-opacity/fill circle that followed Navi. The world shadow
    // direction remains tied only to the current sun or moon and can no longer rotate with the fairy.
    const float disabledLocalLight[4] = { 0.0f, 0.0f, 0.0f, 0.0f };
    interpreter->SetDynamicShadowCaptureState(true, shadowDirection, shadowAnchor, disabledLocalLight);

    // Preserve the user's normal shadow settings. No Navi-dependent opacity reduction is applied.
    const float opacity = CVarGetFloat(CVAR_ENHANCEMENT("Graphics.WorldShadows.Opacity"), 0.20f);
    const float length = CVarGetFloat(CVAR_ENHANCEMENT("Graphics.WorldShadows.Length"), 0.20f);
    const float slabDepth = CVarGetFloat(CVAR_ENHANCEMENT("Graphics.WorldShadows.SlabDepth"), 8.0f);
    const float slabRise = CVarGetFloat(CVAR_ENHANCEMENT("Graphics.WorldShadows.SlabRise"), 8.0f);
    const int edgeSoftness =
        CVarGetInteger(CVAR_ENHANCEMENT("Graphics.WorldShadows.EdgeSoftness"), 0);
    const bool showVolume = CVarGetInteger(CVAR_DEVELOPER_TOOLS("WorldShadows.ShowVolume"), 0) != 0;
    const float minElevation = 0.95f - std::clamp(length, 0.0f, 1.0f) * 0.85f;

    interpreter->SetToonShadowParams(opacity, minElevation, slabDepth, slabRise, edgeSoftness, showVolume);
}

void PreventNaviShadowCaster(void* actorPointer) {
    PlayState* play = gPlayState;
    Actor* actor = static_cast<Actor*>(actorPointer);
    if (play == nullptr || play->state.gfxCtx == nullptr || actor == nullptr || actor->id != ACTOR_EN_ELF ||
        actor->params != FAIRY_NAVI ||
        CVarGetInteger(CVAR_ENHANCEMENT("Graphics.WorldShadows.Enabled"), 0) == 0) {
        return;
    }

    // HandleActorDraw may have armed the generic opaque caster before this later hook executes. Navi is
    // predominantly translucent, so leaving that state armed can leak into the next opaque actor. Close
    // both streams explicitly before any fairy core, glow card, or particle geometry is submitted.
    GraphicsContext* graphicsContext = play->state.gfxCtx;
    gSPToonShadow(graphicsContext->polyOpa.p++, 0, 0, 0, 0.0f);
    gSPToonShadow(graphicsContext->polyXlu.p++, 0, 0, 0, 0.0f);
}

void RegisterNaviShadowFix() {
    COND_HOOK(OnGameFrameUpdate, true, ApplyNaviShadowPolicy);
    COND_HOOK(OnActorDraw, true, PreventNaviShadowCaster);
}

} // namespace

// The Z-prefixed filename sorts after ToonLighting.cpp in CMake's recursive glob, making these hooks
// the final world-shadow policy for each frame and the final caster policy for Navi's actor draw.
static RegisterShipInitFunc initNaviShadowFix(
    RegisterNaviShadowFix,
    { CVAR_ENHANCEMENT("Graphics.ToonLighting.UseNaviLight"),
      CVAR_ENHANCEMENT("Graphics.WorldShadows.Enabled") });
