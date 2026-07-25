from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start_marker: str, end_marker: str, replacement: str, label: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f"{label}: start marker not found")
    end = text.find(end_marker, start + len(start_marker))
    if end < 0:
        raise RuntimeError(f"{label}: end marker not found")
    return text[:start] + replacement + text[end:]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "libultraship/src/fast/interpreter.cpp"
    text = path.read_text(encoding="utf-8")

    # The room display-list traversal is camera dependent: different viewpoints can submit different chunks.
    # Continuously merging those chunks made the world caster set grow whenever the camera rotated, even though
    # neither the sun direction nor the player area had changed. Populate only when the spatial cache recentres.
    merge_start = "    // Merge this frame's room traversal into the persistent cache."
    merge_end = "    // Large dynamic casters join the world cache only for this frame's weak-fill resolve."
    if merge_end not in text:
        # The stability script originally emits the combined pass and the MSVC/layering script changes the
        # following comment. Keep a fallback marker so this final patch remains compatible with either state.
        merge_end = "    // Temporarily append dynamic actor casters to the stable world cache for this resolve only."

    frozen_merge = '''    // Freeze the room caster set inside the current Link-centred spatial cell. Room display-list submission
    // depends on the camera, so accepting newly visible triangles every frame changed the shadow silhouette when
    // only the viewpoint moved. A recenter is deliberately infrequent and is already driven by Link world motion.
    const bool acceptCurrentEnvironmentTraversal = recenterCache || mEnvironmentShadowCasterCache.empty();
    if (acceptCurrentEnvironmentTraversal) {
        for (size_t offset = 0; offset + 8 < mEnvironmentShadowCasterAccum.size(); offset += 9) {
            const float* triangle = &mEnvironmentShadowCasterAccum[offset];
            if (!environmentTriangleValid(triangle)) {
                continue;
            }
            const uint64_t hash = hashTriangle(triangle);
            if (mEnvironmentShadowTriangleHashes.insert(hash).second &&
                mEnvironmentShadowCasterCache.size() + 9 <= kEnvironmentShadowBudgetFloats) {
                mEnvironmentShadowCasterCache.insert(mEnvironmentShadowCasterCache.end(), triangle, triangle + 9);
            }
        }
    }
    mEnvironmentShadowCasterAccum.clear();

'''
    text = replace_between(
        text,
        merge_start,
        merge_end,
        frozen_merge,
        "freeze camera-dependent environment cache",
    )

    # Keep the world shadow projection itself locked to the cache centre. Using mDynamicShadowAnchor here made
    # the light-space grid follow Link every frame, so identical casters sampled different shadow texels as Link
    # or the camera shifted slightly. Actor/Navi passes retain their own per-actor anchors.
    old_world_call = '''            mEnvironmentShadowCasterCache.data(), environmentVertexCount, effectiveCamera, mDynamicShadowLightDir,
            mDynamicShadowAnchor, environmentLocalLight, kDynamicShadowMapResolution,'''
    new_world_call = '''            mEnvironmentShadowCasterCache.data(), environmentVertexCount, effectiveCamera, mDynamicShadowLightDir,
            mEnvironmentShadowCacheAnchor, environmentLocalLight, kDynamicShadowMapResolution,'''
    text = replace_once(text, old_world_call, new_world_call, "stable environment shadow projection anchor")

    path.write_text(text, encoding="utf-8", newline="\n")
    print("Froze camera-dependent room casters inside each spatial cell and locked the world shadow projection anchor.")


if __name__ == "__main__":
    main()
