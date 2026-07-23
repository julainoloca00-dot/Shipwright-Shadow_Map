from pathlib import Path

path = Path("soh/soh/Enhancements/Graphics/ToonLighting.cpp")
text = path.read_text(encoding="utf-8")
old = '''        // The lower bound matters with the extended-culling enhancements: they draw actors BEHIND the
        // camera (negative projected z), which would otherwise pay full capture + volume cost for a
        // shadow that is never visible.
        if (!ToonShadowExcluded(actor) && actor->projectedPos.z < maxDist && actor->projectedPos.z > -100.0f) {
'''
new = '''        // Frustum culling for shadow casters: use the engine's real camera-volume result instead of
        // relying only on projected Z. Extended draw-distance options may still submit off-screen actors,
        // but they no longer pay geometry capture, shadow-map rasterization, or PCF resolve coverage.
        // The player is kept as a safe exception because first-person/cutscene camera modes can briefly
        // update the actor culling flag after the player draw decision.
        const bool shadowCasterInCameraFrustum =
            actor->id == ACTOR_PLAYER || (actor->flags & ACTOR_FLAG_INSIDE_CULLING_VOLUME) != 0;
        if (!ToonShadowExcluded(actor) && shadowCasterInCameraFrustum && actor->projectedPos.z < maxDist &&
            actor->projectedPos.z > -100.0f) {
'''
if text.count(old) != 1:
    raise RuntimeError(f"Expected one shadow culling block, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Camera-frustum shadow caster culling applied.")
