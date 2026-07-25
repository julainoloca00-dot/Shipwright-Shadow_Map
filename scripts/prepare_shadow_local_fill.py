from pathlib import Path


root = Path(__file__).resolve().parents[1]
path = root / "libultraship/src/fast/backends/gfx_direct3d11_shadow_map.inc"
text = path.read_text(encoding="utf-8")

# apply_realistic_shadow_fixes.py intentionally removes the old radial local-fill block. The final
# Navi layering pass needs that block as a stable intermediate representation so it can replace it
# with the signed-radius weak-world/strong-actor implementation. Normalize both the regular and
# MSAA shaders here, while remaining safe if a future source already contains either representation.
removed_fill = '''    const float shadow = 1.0 - visibility;
    // Local lights now steer the shadow-map direction on the game side. They must not erase opacity
    // radially here: that produced a bright halo around Navi and made 100% opacity vary with distance.
    return float4(0.0, 0.0, 0.0, saturate(shadowParams.x * shadow * edgeFade));'''

legacy_fill = '''    const float shadow = 1.0 - visibility;
    float localFill = 0.0;
    if (localLight.w > 0.0) {
        localFill = saturate(1.0 - length(world.xyz - localLight.xyz) / localLight.w);
        localFill = localFill * localFill * (3.0 - 2.0 * localFill);
    }
    const float localShadowScale = 1.0 - localFill * 0.85;
    return float4(0.0, 0.0, 0.0,
                  saturate(shadowParams.x * shadow * edgeFade * localShadowScale));'''

removed_count = text.count(removed_fill)
legacy_count = text.count("const float localShadowScale = 1.0 - localFill * 0.85;")
layered_count = text.count("const float localFillStrength = localLight.w < 0.0 ? 0.35 : 0.85;")

if removed_count == 2:
    text = text.replace(removed_fill, legacy_fill)
elif legacy_count == 2:
    pass
elif layered_count == 2:
    # Already normalized and upgraded; keep the operation idempotent.
    pass
else:
    raise RuntimeError(
        "Navi local-fill normalization: expected two removed, legacy, or layered shader blocks; "
        f"found removed={removed_count}, legacy={legacy_count}, layered={layered_count}"
    )

path.write_text(text, encoding="utf-8", newline="\n")
print("Prepared both DX11 shadow resolve shaders for layered Navi local fill.")
