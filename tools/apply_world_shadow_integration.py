from pathlib import Path

path = Path("soh/soh/Enhancements/Graphics/ToonLighting.cpp")
text = path.read_text(encoding="utf-8")

old_decl = "static void ToonClearKeyStates(); // defined with the key-state map below\n"
new_decl = (
    old_decl
    + "static void ToonEnvKey(PlayState* play, f32 dirOut[3], f32 colOut[3]); // stable world-shadow light\n"
)
if new_decl not in text:
    if text.count(old_decl) != 1:
        raise RuntimeError("ToonEnvKey declaration insertion point not unique")
    text = text.replace(old_decl, new_decl, 1)

old_update = """    RefreshFrameParams();
    // Clear before any early-out, so the dedup state resets even on a headless window (no renderer)."""
new_update = """    RefreshFrameParams();

    // Dynamic shadow mapping uses one stable directional light and one stable world-space anchor.
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
    }

    // Clear before any early-out, so the dedup state resets even on a headless window (no renderer)."""
if new_update not in text:
    if text.count(old_update) != 1:
        raise RuntimeError("OnToonFrameUpdate insertion point not unique")
    text = text.replace(old_update, new_update, 1)

path.write_text(text, encoding="utf-8", newline="\n")
