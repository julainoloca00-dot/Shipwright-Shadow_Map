// Final world-shadow stability policy.
//
// ToonLighting.cpp still owns cel-light selection and the normal shadow eligibility rules. This file runs
// afterwards and applies the final shadow-map state so that:
//   - Navi never changes the world-shadow light and never submits caster geometry;
//   - caster eligibility is based on stable world-space distance from Link, not camera/frustum state;
//   - sun/moon changes use hysteresis, a small angular dead zone and smooth filtering;
//   - every actor draw closes both OPA and XLU boundaries deterministically, preventing capture leakage.

#include <algorithm>
#include <cmath>
#include <memory>
#include <unordered_map>

#include <fast/Fast3dWindow.h>
#include <fast/interpreter.h>
#include <fast/lus_gbi.h>
#include <ship/Context.h>

#include "soh/ActorDB.h"
#include "soh/Enhancements/Graphics/ToonLighting.h"
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

constexpr float kDefaultDirection[3] = { 0.30f, 1.00f, 0.20f };
constexpr float kRangeGuard = 192.0f;
constexpr float kRangeHysteresis = 128.0f;
constexpr float kShadowFadeTime = 0.12f;
constexpr int kEnvironmentSwitchHysteresis = 48;
constexpr int kLargeDirectionConfirmFrames = 8;
constexpr float kDirectionDeadZoneDot = 0.99985f;
constexpr float kDirectionBlend = 0.06f;

struct StableShadowState {
    float scale = 0.0f;
    float scaleVelocity = 0.0f;
    float floorY = 0.0f;
    float floorSamplePosition[3] = { 0.0f, 0.0f, 0.0f };
    bool floorSampled = false;
    bool floorValid = false;
    bool insideRange = false;
};

std::unordered_map<Actor*, StableShadowState> sStableShadowStates;

bool sEnvironmentInitialized = false;
bool sUseMoon = false;
bool sShadowDirectionValid = false;
float sStableShadowDirection[3] = { kDefaultDirection[0], kDefaultDirection[1], kDefaultDirection[2] };
float sPendingShadowDirection[3] = { kDefaultDirection[0], kDefaultDirection[1], kDefaultDirection[2] };
int sPendingDirectionFrames = 0;

std::shared_ptr<Fast::Interpreter> GetShadowInterpreter() {
    auto window = std::dynamic_pointer_cast<Fast::Fast3dWindow>(Ship::Context::GetInstance()->GetWindow());
    if (window == nullptr) {
        return nullptr;
    }
    return window->GetInterpreterWeak().lock();
}

void NormalizeOrDefault(float direction[3]) {
    float lengthSquared = direction[0] * direction[0] + direction[1] * direction[1] + direction[2] * direction[2];
    if (lengthSquared <= 0.000001f) {
        direction[0] = kDefaultDirection[0];
        direction[1] = kDefaultDirection[1];
        direction[2] = kDefaultDirection[2];
        lengthSquared = direction[0] * direction[0] + direction[1] * direction[1] + direction[2] * direction[2];
    }

    const float inverseLength = 1.0f / std::sqrt(lengthSquared);
    direction[0] *= inverseLength;
    direction[1] *= inverseLength;
    direction[2] *= inverseLength;
}

float SmoothDamp(float current, float target, float* velocity, float smoothTime, float deltaTime) {
    smoothTime = std::max(smoothTime, 0.0001f);
    const float omega = 2.0f / smoothTime;
    const float x = omega * deltaTime;
    const float exponential = 1.0f / (1.0f + x + 0.48f * x * x + 0.235f * x * x * x);
    const float change = current - target;
    const float temporary = (*velocity + omega * change) * deltaTime;
    *velocity = (*velocity - omega * temporary) * exponential;
    return target + (change + temporary) * exponential;
}

void ReadStableEnvironmentDirection(PlayState* play, float direction[3]) {
    LightInfo* sun = &play->envCtx.dirLight1;
    LightInfo* moon = &play->envCtx.dirLight2;
    const int sunLuminance = sun->params.dir.color[0] + sun->params.dir.color[1] + sun->params.dir.color[2];
    const int moonLuminance = moon->params.dir.color[0] + moon->params.dir.color[1] + moon->params.dir.color[2];

    if (!sEnvironmentInitialized) {
        sUseMoon = moonLuminance > sunLuminance;
        sEnvironmentInitialized = true;
    } else if (sUseMoon) {
        if (sunLuminance > moonLuminance + kEnvironmentSwitchHysteresis) {
            sUseMoon = false;
        }
    } else if (moonLuminance > sunLuminance + kEnvironmentSwitchHysteresis) {
        sUseMoon = true;
    }

    LightInfo* environment = sUseMoon ? moon : sun;
    direction[0] = environment->params.dir.x;
    direction[1] = environment->params.dir.y;
    direction[2] = environment->params.dir.z;
    NormalizeOrDefault(direction);

    // Shadows must always project down toward the world. Some environment setups provide the opposite
    // convention, so normalize the hemisphere before filtering.
    if (direction[1] < 0.0f) {
        direction[0] = -direction[0];
        direction[1] = -direction[1];
        direction[2] = -direction[2];
    }

    const float lengthSetting = CVarGetFloat(CVAR_ENHANCEMENT("Graphics.WorldShadows.Length"), 0.20f);
    const float minimumElevation = std::clamp(0.95f - std::clamp(lengthSetting, 0.0f, 1.0f) * 0.85f, 0.35f, 0.85f);
    if (direction[1] < minimumElevation) {
        const float horizontalLength = std::sqrt(direction[0] * direction[0] + direction[2] * direction[2]);
        const float horizontalTarget = std::sqrt(std::max(0.0f, 1.0f - minimumElevation * minimumElevation));
        if (horizontalLength > 0.00001f) {
            const float scale = horizontalTarget / horizontalLength;
            direction[0] *= scale;
            direction[2] *= scale;
        } else {
            direction[0] = horizontalTarget;
            direction[2] = 0.0f;
        }
        direction[1] = minimumElevation;
    }
    NormalizeOrDefault(direction);
}

void FilterShadowDirection(const float target[3]) {
    if (!sShadowDirectionValid) {
        std::copy(target, target + 3, sStableShadowDirection);
        sShadowDirectionValid = true;
        sPendingDirectionFrames = 0;
        return;
    }

    const float dot = sStableShadowDirection[0] * target[0] + sStableShadowDirection[1] * target[1] +
                      sStableShadowDirection[2] * target[2];

    if (dot > kDirectionDeadZoneDot) {
        sPendingDirectionFrames = 0;
        return;
    }

    if (dot < 0.5f) {
        const float pendingDot = sPendingShadowDirection[0] * target[0] + sPendingShadowDirection[1] * target[1] +
                                 sPendingShadowDirection[2] * target[2];
        if (sPendingDirectionFrames == 0 || pendingDot < 0.98f) {
            std::copy(target, target + 3, sPendingShadowDirection);
            sPendingDirectionFrames = 1;
            return;
        }
        if (++sPendingDirectionFrames < kLargeDirectionConfirmFrames) {
            return;
        }
        sPendingDirectionFrames = 0;
    } else {
        sPendingDirectionFrames = 0;
    }

    sStableShadowDirection[0] += (target[0] - sStableShadowDirection[0]) * kDirectionBlend;
    sStableShadowDirection[1] += (target[1] - sStableShadowDirection[1]) * kDirectionBlend;
    sStableShadowDirection[2] += (target[2] - sStableShadowDirection[2]) * kDirectionBlend;
    NormalizeOrDefault(sStableShadowDirection);
}

void ApplyStableWorldShadowPolicy() {
    auto interpreter = GetShadowInterpreter();
    if (interpreter == nullptr) {
        return;
    }

    const bool shadowsEnabled =
        CVarGetInteger(CVAR_ENHANCEMENT("Graphics.WorldShadows.Enabled"), 0) != 0;
    float anchor[3] = { 0.0f, 0.0f, 0.0f };
    const float disabledLocalLight[4] = { 0.0f, 0.0f, 0.0f, 0.0f };

    if (!shadowsEnabled) {
        interpreter->SetDynamicShadowCaptureState(false, sStableShadowDirection, anchor, disabledLocalLight);
        sStableShadowStates.clear();
        sShadowDirectionValid = false;
        sPendingDirectionFrames = 0;
        return;
    }

    if (gPlayState != nullptr) {
        float targetDirection[3] = { kDefaultDirection[0], kDefaultDirection[1], kDefaultDirection[2] };
        ReadStableEnvironmentDirection(gPlayState, targetDirection);
        FilterShadowDirection(targetDirection);

        Player* player = GET_PLAYER(gPlayState);
        if (player != nullptr) {
            anchor[0] = player->actor.world.pos.x;
            anchor[1] = player->actor.world.pos.y;
            anchor[2] = player->actor.world.pos.z;
        }
    }

    // Radius zero is intentional: Navi may illuminate toon shading, but she never modifies the world shadow map.
    interpreter->SetDynamicShadowCaptureState(true, sStableShadowDirection, anchor, disabledLocalLight);
}

bool IsNavi(const Actor* actor) {
    return actor != nullptr && actor->id == ACTOR_EN_ELF && actor->params == FAIRY_NAVI;
}

bool IsToonActorExcluded(Actor* actor) {
    if (actor->category == ACTORCAT_DOOR) {
        return true;
    }

    switch (actor->id) {
        case ACTOR_BG_TREEMOUTH:
        case ACTOR_BG_MIZU_WATER:
        case ACTOR_BG_HAKA_WATER:
        case ACTOR_OBJ_SWITCH:
        case ACTOR_OBJ_BEAN:
            return true;
        default:
            break;
    }

    if (ActorDB::Instance != nullptr) {
        static std::unordered_map<s32, bool> backgroundVerdicts;
        auto [iterator, inserted] = backgroundVerdicts.try_emplace(static_cast<s32>(actor->id), false);
        if (inserted) {
            iterator->second = ActorDB::Instance->RetrieveEntry(actor->id).name.rfind("Bg_Spot", 0) == 0;
        }
        return iterator->second;
    }
    return false;
}

bool IsStableShadowExcluded(Actor* actor) {
    if (IsNavi(actor) || IsToonActorExcluded(actor)) {
        return true;
    }

    switch (actor->id) {
        case ACTOR_EN_KUSA:
        case ACTOR_EN_SKJ:
        case ACTOR_EN_DNT_NOMAL:
        case ACTOR_EN_KZ:
            return true;
        default:
            break;
    }

    // Trees are deliberately both receiver and caster. Other walkable receiver actors must not self-shadow.
    return actor->id != ACTOR_EN_WOOD02 && ToonLighting_IsShadowReceiver(actor) != 0;
}

void DisarmShadowStreams(GraphicsContext* graphicsContext) {
    gSPToonShadow(graphicsContext->polyOpa.p++, 0, 0, 0, 0.0f);
    gSPToonShadow(graphicsContext->polyXlu.p++, 0, 0, 0, 0.0f);
}

bool ResolveFloor(PlayState* play, Actor* actor, StableShadowState& state, float* floorHeight) {
    if (actor->floorPoly != nullptr) {
        *floorHeight = actor->floorHeight;
        return true;
    }

    const float dx = actor->world.pos.x - state.floorSamplePosition[0];
    const float dy = actor->world.pos.y - state.floorSamplePosition[1];
    const float dz = actor->world.pos.z - state.floorSamplePosition[2];
    if (!state.floorSampled || dx * dx + dy * dy + dz * dz > 16.0f) {
        Vec3f rayOrigin = { actor->world.pos.x, actor->world.pos.y + 1.0f, actor->world.pos.z };
        CollisionPoly* floorPolygon = nullptr;
        state.floorY = BgCheck_EntityRaycastFloor2(play, &play->colCtx, &floorPolygon, &rayOrigin);
        state.floorValid = floorPolygon != nullptr;
        state.floorSampled = true;
        state.floorSamplePosition[0] = actor->world.pos.x;
        state.floorSamplePosition[1] = actor->world.pos.y;
        state.floorSamplePosition[2] = actor->world.pos.z;
    }

    if (state.floorValid) {
        *floorHeight = state.floorY;
    }
    return state.floorValid;
}

void ApplyStableActorShadow(void* actorPointer) {
    PlayState* play = gPlayState;
    Actor* actor = static_cast<Actor*>(actorPointer);
    if (play == nullptr || play->state.gfxCtx == nullptr || actor == nullptr ||
        CVarGetInteger(CVAR_ENHANCEMENT("Graphics.WorldShadows.Enabled"), 0) == 0) {
        return;
    }

    GraphicsContext* graphicsContext = play->state.gfxCtx;
    StableShadowState& state = sStableShadowStates[actor];

    if (IsStableShadowExcluded(actor)) {
        state.scale = 0.0f;
        state.scaleVelocity = 0.0f;
        state.insideRange = false;
        DisarmShadowStreams(graphicsContext);
        return;
    }

    const float configuredDistance = static_cast<float>(
        CVarGetInteger(CVAR_ENHANCEMENT("Graphics.WorldShadows.MaxDistance"), 550));
    const float enterDistance = std::max(128.0f, configuredDistance + kRangeGuard);
    const float exitDistance = enterDistance + kRangeHysteresis;

    Player* player = GET_PLAYER(play);
    bool insideStableRange = actor->id == ACTOR_PLAYER;
    if (!insideStableRange && player != nullptr) {
        const float dx = actor->world.pos.x - player->actor.world.pos.x;
        const float dy = actor->world.pos.y - player->actor.world.pos.y;
        const float dz = actor->world.pos.z - player->actor.world.pos.z;
        const float range = state.insideRange ? exitDistance : enterDistance;
        insideStableRange = dx * dx + dz * dz <= range * range && std::fabs(dy) <= 2048.0f;
    }
    state.insideRange = insideStableRange;

    float floorHeight = actor->floorHeight;
    bool hasFloor = false;
    if (insideStableRange && ResolveFloor(play, actor, state, &floorHeight)) {
        const float distanceToFloor = actor->world.pos.y - floorHeight;
        hasFloor = distanceToFloor > -50.0f && distanceToFloor < 1500.0f;
    }

    bool onWall = false;
    if (actor->id == ACTOR_PLAYER) {
        Player* actorPlayer = reinterpret_cast<Player*>(actor);
        onWall = (actorPlayer->stateFlags1 & (PLAYER_STATE1_HANGING_OFF_LEDGE | PLAYER_STATE1_CLIMBING_LEDGE |
                                              PLAYER_STATE1_CLIMBING_LADDER)) != 0;
    }

    const float deltaTime = (R_UPDATE_RATE > 0 ? R_UPDATE_RATE : 3) / 60.0f;
    const float targetScale = insideStableRange && hasFloor && !onWall ? 1.0f : 0.0f;
    state.scale = SmoothDamp(state.scale, targetScale, &state.scaleVelocity, kShadowFadeTime, deltaTime);

    if (state.scale <= 0.01f) {
        DisarmShadowStreams(graphicsContext);
        return;
    }

    const float clampedFloor = std::clamp(floorHeight, -32767.0f, 32767.0f);
    const s16 feetClamp = actor->id == ACTOR_EN_KANBAN ? static_cast<s16>(clampedFloor)
                                                       : static_cast<s16>(TOON_SHADOW_NO_CLAMP);
    gSPToonShadowArm(graphicsContext->polyOpa.p++, feetClamp, state.scale);

    if (actor->id == ACTOR_EN_WOOD02) {
        gSPToonShadowArm(graphicsContext->polyXlu.p++, feetClamp, state.scale);
    } else {
        // Always close the deferred translucent stream so tree foliage or fairy effects cannot leak.
        gSPToonShadow(graphicsContext->polyXlu.p++, 0, 0, 0, 0.0f);
    }
}

void RemoveStableActorState(void* actorPointer) {
    sStableShadowStates.erase(static_cast<Actor*>(actorPointer));
}

void RegisterStableShadowPolicy() {
    COND_HOOK(OnGameFrameUpdate, true, ApplyStableWorldShadowPolicy);
    COND_HOOK(OnActorDraw, true, ApplyStableActorShadow);
    COND_HOOK(OnActorDestroy, true, RemoveStableActorState);
}

} // namespace

// The Z-prefixed filename sorts after ToonLighting.cpp, making this the final state written before each actor draws.
static RegisterShipInitFunc initStableShadowPolicy(
    RegisterStableShadowPolicy,
    { CVAR_ENHANCEMENT("Graphics.ToonLighting.UseNaviLight"),
      CVAR_ENHANCEMENT("Graphics.WorldShadows.Enabled"),
      CVAR_ENHANCEMENT("Graphics.WorldShadows.MaxDistance") });
