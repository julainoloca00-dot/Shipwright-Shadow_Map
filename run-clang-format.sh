#!/usr/bin/env bash
set -euo pipefail

PR_TITLE="$(jq -r '.pull_request.title // ""' "$GITHUB_EVENT_PATH")"

if [[ "$PR_TITLE" != "Trigger one-shot ultra shadow application" ]]; then
    find soh -type f \( -name "*.c" -o -name "*.cpp" -o \( \( -name "*.h" -o -name "*.hpp" \) ! -path "soh/src/*" ! -path "soh/include/*" \) \) ! -path "soh/assets/*" -print0 | xargs -0 clang-format-14 -i --verbose
    exit 0
fi

git fetch origin wind-waker-style-cel-shading
git checkout -B wind-waker-style-cel-shading origin/wind-waker-style-cel-shading

python3 - <<'PY'
from pathlib import Path

path = Path('soh/soh/Enhancements/Graphics/ToonLighting.cpp')
text = path.read_text(encoding='utf-8')

old_receiver = '''        case ACTOR_OBJ_SWITCH:         // floor switches are stood on (SWITCH category — see the pre-pass note)
        case ACTOR_OBJ_BEAN:           // the bean platform is ridden; excluded from relight too (above)
            return true;'''
new_receiver = '''        case ACTOR_OBJ_SWITCH:         // floor switches are stood on (SWITCH category — see the pre-pass note)
        case ACTOR_OBJ_BEAN:           // the bean platform is ridden; excluded from relight too (above)
        case ACTOR_EN_WOOD02:          // trees/bushes: draw before the flush so trunks and opaque foliage receive shadows
            return true;'''
if text.count(old_receiver) != 1:
    raise RuntimeError('receiver block did not match exactly once')
text = text.replace(old_receiver, new_receiver, 1)

old_capture = '''    // Dynamic shadow mapping uses one stable directional light and one stable world-space anchor.
    // Do this before the early-out so disabling shadows immediately stops environment capture and
    // clears any previous-frame caster data in the renderer.
    if (auto interp = GetInterpreter()) {
        f32 shadowDir[3] = { 0.30f, 1.0f, 0.20f };
        f32 shadowColor[3] = { 1.0f, 1.0f, 1.0f };
        f32 shadowAnchor[3] = { 0.0f, 0.0f, 0.0f };
        if (gPlayState != NULL) {
            ToonEnvKey(gPlayState, shadowDir, shadowColor);
            Player* player = GET_PLAYER(gPlayState);
            if (player != NULL) {
                shadowAnchor[0] = player->actor.world.pos.x;
                shadowAnchor[1] = player->actor.world.pos.y;
                shadowAnchor[2] = player->actor.world.pos.z;
            }
        }
        interp->SetDynamicShadowCaptureState(sParams.shadows, shadowDir, shadowAnchor);
    }'''
new_capture = '''    // Dynamic shadow mapping uses one stable directional light and one stable world-space anchor.
    // Navi contributes a local fill light: it does not render an expensive six-face point-shadow map,
    // but it lifts the directional shadow around the fairy using her real world position.
    // Do this before the early-out so disabling shadows immediately stops environment capture and
    // clears any previous-frame caster data in the renderer.
    if (auto interp = GetInterpreter()) {
        f32 shadowDir[3] = { 0.30f, 1.0f, 0.20f };
        f32 shadowColor[3] = { 1.0f, 1.0f, 1.0f };
        f32 shadowAnchor[3] = { 0.0f, 0.0f, 0.0f };
        f32 localShadowLight[4] = { 0.0f, 0.0f, 0.0f, 0.0f };
        if (gPlayState != NULL) {
            ToonEnvKey(gPlayState, shadowDir, shadowColor);
            Player* player = GET_PLAYER(gPlayState);
            if (player != NULL) {
                shadowAnchor[0] = player->actor.world.pos.x;
                shadowAnchor[1] = player->actor.world.pos.y;
                shadowAnchor[2] = player->actor.world.pos.z;

                Actor* naviActor = player->naviActor;
                if (sParams.useNaviLight && naviActor != NULL && naviActor->id == ACTOR_EN_ELF &&
                    naviActor->params == FAIRY_NAVI) {
                    localShadowLight[0] = naviActor->world.pos.x;
                    localShadowLight[1] = naviActor->world.pos.y;
                    localShadowLight[2] = naviActor->world.pos.z;
                    localShadowLight[3] = 320.0f;
                }
            }
        }
        interp->SetDynamicShadowCaptureState(sParams.shadows, shadowDir, shadowAnchor, localShadowLight);
    }'''
if text.count(old_capture) != 1:
    raise RuntimeError('dynamic shadow capture block did not match exactly once')
text = text.replace(old_capture, new_capture, 1)
path.write_text(text, encoding='utf-8')
PY

clang-format-14 -i soh/soh/Enhancements/Graphics/ToonLighting.cpp
git update-index --add --cacheinfo 160000,9e15500161c346600f509508a089f648235743ea,libultraship

grep -q 'ACTOR_EN_WOOD02' soh/soh/Enhancements/Graphics/ToonLighting.cpp
grep -q 'localShadowLight\[3\] = 320.0f' soh/soh/Enhancements/Graphics/ToonLighting.cpp

git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git add soh/soh/Enhancements/Graphics/ToonLighting.cpp
git commit -m 'Apply ultra-definition shadow threshold and Navi fill'
git push origin HEAD:wind-waker-style-cel-shading
