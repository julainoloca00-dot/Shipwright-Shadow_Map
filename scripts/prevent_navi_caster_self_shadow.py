from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_game_shadow_policy(root: Path) -> None:
    path = root / "soh/soh/Enhancements/Graphics/ZZNaviShadowLightFix.cpp"
    text = path.read_text(encoding="utf-8")

    if "Use the collision floor as the shadow receiver plane" not in text:
        text = replace_once(
            text,
            """            anchor[0] = player->actor.world.pos.x;
            anchor[1] = player->actor.world.pos.y;
            anchor[2] = player->actor.world.pos.z;""",
            """            anchor[0] = player->actor.world.pos.x;
            // Use the collision floor as the shadow receiver plane. Link's animated model centre must not move
            // this height or make his compact shadow projection swim while the camera/animation changes.
            anchor[1] =
                player->actor.floorPoly != nullptr ? player->actor.floorHeight : player->actor.world.pos.y;
            anchor[2] = player->actor.world.pos.z;""",
            "player floor-height shadow anchor",
        )

    if "Publish the resolved collision floor for every compact caster" not in text:
        text = replace_once(
            text,
            """    const float clampedFloor = std::clamp(floorHeight, -32767.0f, 32767.0f);
    const s16 feetClamp = actor->id == ACTOR_EN_KANBAN ? static_cast<s16>(clampedFloor)
                                                       : static_cast<s16>(TOON_SHADOW_NO_CLAMP);""",
            """    const float clampedFloor = std::clamp(floorHeight, -32767.0f, 32767.0f);
    // Publish the resolved collision floor for every compact caster. The independent receiver mask uses this
    // exact plane to reject the caster's own body and nearby vertical walls.
    const s16 feetClamp = static_cast<s16>(clampedFloor);""",
            "publish compact-caster collision floor",
        )

    path.write_text(text, encoding="utf-8", newline="\n")


def patch_dx11_receiver_sentinel(root: Path) -> None:
    path = root / "libultraship/src/fast/backends/gfx_direct3d11_shadow_map.inc"
    text = path.read_text(encoding="utf-8")

    old_shader_block = """    float localFill = 0.0;
    const float localRadius = abs(localLight.w);
    if (localRadius > 0.0) {
        localFill = saturate(1.0 - length(world.xyz - localLight.xyz) / localRadius);
        localFill = localFill * localFill * (3.0 - 2.0 * localFill);
    }
    const float localFillStrength = localLight.w < 0.0 ? 0.35 : 0.85;
    const float localShadowScale = 1.0 - localFill * localFillStrength;"""
    new_shader_block = """    const bool casterReceiverOnly = localLight.w <= -4096.0;
    if (casterReceiverOnly && world.y > localLight.x) discard;

    float localFill = 0.0;
    const float localRadius = casterReceiverOnly ? 0.0 : abs(localLight.w);
    if (localRadius > 0.0) {
        localFill = saturate(1.0 - length(world.xyz - localLight.xyz) / localRadius);
        localFill = localFill * localFill * (3.0 - 2.0 * localFill);
    }
    const float localFillStrength = (!casterReceiverOnly && localLight.w < 0.0) ? 0.35 : 0.85;
    const float localShadowScale = 1.0 - localFill * localFillStrength;"""

    shader_matches = text.count(old_shader_block)
    if shader_matches != 2:
        raise RuntimeError(f"compact receiver sentinel shader: expected two matches, found {shader_matches}")
    text = text.replace(old_shader_block, new_shader_block)
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    patch_game_shadow_policy(root)
    patch_dx11_receiver_sentinel(root)
    print("Published real caster floors and enabled compact receiver filtering in both DX11 resolve shaders.")


if __name__ == "__main__":
    main()
