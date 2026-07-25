from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    root = Path(__file__).resolve().parents[1]

    # Make the actual Navi point light obey the same state that controls whether Navi is active/visible.
    # This keeps the world-light circle, actor toon lighting and shadow clearing synchronized at the source.
    elf_path = root / "soh/src/overlays/actors/ovl_En_Elf/z_en_elf.c"
    elf_text = elf_path.read_text(encoding="utf-8")
    old_radius = '''    glowLightRadius = 100;

    if (this->unk_2A8 == 8) {
        glowLightRadius = 0;
    }'''
    new_radius = '''    glowLightRadius = 100;

    // player->naviActor remains allocated while Navi is hidden. PLAYER_STATE2_NAVI_ACTIVE is the authoritative
    // gameplay state changed by the vanish/return logic, so the emitted point light must follow it as well.
    player = GET_PLAYER(play);
    const s32 naviVisiblyActive =
        player != NULL && (player->stateFlags2 & PLAYER_STATE2_NAVI_ACTIVE) != 0;
    if ((this->actor.params == FAIRY_NAVI &&
         (!naviVisiblyActive || this->unk_2A8 == 7 || this->unk_2A8 == 8 || this->actor.scale.x <= 0.00008f)) ||
        (this->actor.params != FAIRY_NAVI && this->unk_2A8 == 8)) {
        glowLightRadius = 0;
    }'''
    elf_text = replace_once(elf_text, old_radius, new_radius, "Navi source-light active-state gate")
    elf_path.write_text(elf_text, encoding="utf-8", newline="\n")

    # Keep a renderer-side safety gate too. Even if hook ordering observes the previous frame's LightInfo,
    # a hidden Navi can never publish a positive local shadow-fill radius.
    policy_path = root / "soh/soh/Enhancements/Graphics/ZZNaviShadowLightFix.cpp"
    policy_text = policy_path.read_text(encoding="utf-8")
    old_visibility = '''                const bool visiblyEmitting = navi->unk_2A8 != 8 && (navi->fairyFlags & 8) == 0 &&
                                               visualScale > 0.01f && emittedRadius > 0.01f;'''
    new_visibility = '''                const bool naviGameplayActive =
                    (player->stateFlags2 & PLAYER_STATE2_NAVI_ACTIVE) != 0;
                const bool visiblyEmitting = naviGameplayActive && navi->unk_2A8 != 7 && navi->unk_2A8 != 8 &&
                                               (navi->fairyFlags & 8) == 0 && visualScale > 0.01f &&
                                               emittedRadius > 0.01f;'''
    policy_text = replace_once(
        policy_text,
        old_visibility,
        new_visibility,
        "Navi renderer active-state gate",
    )
    policy_path.write_text(policy_text, encoding="utf-8", newline="\n")

    print("Bound Navi world light and shadow fill to PLAYER_STATE2_NAVI_ACTIVE.")


if __name__ == "__main__":
    main()
