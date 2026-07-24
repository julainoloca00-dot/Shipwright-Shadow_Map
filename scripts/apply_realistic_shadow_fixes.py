from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_interpreter(root: Path) -> None:
    path = root / "libultraship/src/fast/interpreter.cpp"
    text = path.read_text(encoding="utf-8")

    old_gated = '''    // SOH [Enhancement] Actor shadow: while the shadow pass is armed for this object, record its world-space
    // triangles here (before any culling, so the whole silhouette is captured). FlushToonShadow drains them
    // at the object boundary. is_rect screen-space quads (UI) have no world position, so skip them. The
    // replayed shadow geometry itself runs with toon_shadow cleared, so it is never re-captured. NOTE: this
    // is gated on toon_shadow only (NOT mRdp->toon), so shadows work even when the cel relight is disabled.
    if (mRdp->toon_shadow && !is_rect && (mRsp->geometry_mode & G_LIGHTING)) {
        for (int si = 0; si < 3; si++) {
            mShadowVerts.push_back(v_arr[si]->wx);
            mShadowVerts.push_back(v_arr[si]->wy);
            mShadowVerts.push_back(v_arr[si]->wz);
        }
    }
'''
    old_ungated = old_gated.replace(' && (mRsp->geometry_mode & G_LIGHTING)', '')

    new = r'''    // SOH [Enhancement] Actor shadow capture. Opaque/lit geometry is stored directly. Unlit geometry
    // only reaches this path while En_Wood02 has armed POLY_XLU, so it is a tree canopy card. Canopy cards
    // are tessellated and alpha-tested against the currently bound N64 texture on the CPU. This preserves
    // the real holes in leaf textures without replaying the entire Fast3D material pipeline in the depth pass.
    if (mRdp->toon_shadow && !is_rect) {
        const bool foliageCard = (mRsp->geometry_mode & G_LIGHTING) == 0;

        auto appendShadowTriangle = [this](float ax, float ay, float az, float bx, float by, float bz, float cx,
                                           float cy, float cz) {
            mShadowVerts.push_back(ax);
            mShadowVerts.push_back(ay);
            mShadowVerts.push_back(az);
            mShadowVerts.push_back(bx);
            mShadowVerts.push_back(by);
            mShadowVerts.push_back(bz);
            mShadowVerts.push_back(cx);
            mShadowVerts.push_back(cy);
            mShadowVerts.push_back(cz);
        };

        if (!foliageCard) {
            appendShadowTriangle(v1->wx, v1->wy, v1->wz, v2->wx, v2->wy, v2->wz, v3->wx, v3->wy, v3->wz);
        } else {
            struct ShadowFoliageVertex {
                float x, y, z;
                float u, v;
            };

            auto sampleFoliageAlpha = [this](float rawU, float rawV) -> float {
                const uint32_t tileIndex = mRdp->first_tile_index;
                if (tileIndex >= 8) {
                    return -1.0f;
                }

                const auto& tile = mRdp->texture_tile[tileIndex];
                const auto& texture = mRdp->loaded_texture[tile.tmem_index];
                const uint8_t* address = texture.addr;
                if (address == nullptr || tile.line_size_bytes == 0) {
                    return -1.0f;
                }

                uint32_t width = tile.line_size_bytes;
                switch (tile.siz) {
                    case G_IM_SIZ_4b:
                        width *= 2;
                        break;
                    case G_IM_SIZ_8b:
                        break;
                    case G_IM_SIZ_16b:
                        width /= 2;
                        break;
                    case G_IM_SIZ_32b:
                        width /= 4;
                        break;
                    default:
                        return -1.0f;
                }
                if (width == 0) {
                    return -1.0f;
                }

                uint32_t rowBytes = texture.full_image_line_size_bytes;
                if (rowBytes == 0) {
                    rowBytes = texture.line_size_bytes;
                }
                if (rowBytes == 0) {
                    rowBytes = tile.line_size_bytes;
                }

                uint32_t height = 0;
                if (texture.raw_tex_metadata.height > 0) {
                    height = texture.raw_tex_metadata.height;
                } else if (rowBytes > 0) {
                    height = texture.orig_size_bytes / rowBytes;
                }
                if (height == 0) {
                    height = 1;
                }

                float u = rawU / 32.0f;
                float v = rawV / 32.0f;
                if (tile.shifts != 0) {
                    u = tile.shifts <= 10 ? u / static_cast<float>(1 << tile.shifts)
                                          : u * static_cast<float>(1 << (16 - tile.shifts));
                }
                if (tile.shiftt != 0) {
                    v = tile.shiftt <= 10 ? v / static_cast<float>(1 << tile.shiftt)
                                          : v * static_cast<float>(1 << (16 - tile.shiftt));
                }
                u -= tile.uls / 4.0f;
                v -= tile.ult / 4.0f;

                auto resolveCoord = [](int value, int size, uint8_t mode) {
                    if (size <= 1) {
                        return 0;
                    }
                    if ((mode & G_TX_CLAMP) != 0) {
                        return std::clamp(value, 0, size - 1);
                    }
                    if ((mode & G_TX_MIRROR) != 0) {
                        const int period = size * 2;
                        value %= period;
                        if (value < 0) {
                            value += period;
                        }
                        if (value >= size) {
                            value = period - 1 - value;
                        }
                        return value;
                    }
                    value %= size;
                    if (value < 0) {
                        value += size;
                    }
                    return value;
                };

                const int x = resolveCoord(static_cast<int>(floorf(u)), static_cast<int>(width), tile.cms);
                const int y = resolveCoord(static_cast<int>(floorf(v)), static_cast<int>(height), tile.cmt);
                const size_t rowOffset = static_cast<size_t>(y) * rowBytes;
                const size_t availableBytes =
                    texture.raw_tex_metadata.resource != nullptr
                        ? texture.raw_tex_metadata.resource->ImageDataSize
                        : std::max<size_t>(texture.size_bytes, texture.orig_size_bytes);

                auto byteAt = [&](size_t offset) -> int {
                    return offset < availableBytes ? static_cast<int>(address[offset]) : -1;
                };
                auto paletteAlpha = [this](uint32_t index) -> float {
                    const uint8_t* palette = mRdp->palettes[(index / 128) & 1];
                    if (palette == nullptr) {
                        return -1.0f;
                    }
                    const size_t offset = static_cast<size_t>(index % 128) * 2;
                    const uint16_t color = static_cast<uint16_t>((palette[offset] << 8) | palette[offset + 1]);
                    return (color & 1) != 0 ? 1.0f : 0.0f;
                };

                if ((texture.tex_flags & TEX_FLAG_LOAD_AS_IMG) != 0 && texture.raw_tex_metadata.width > 0 &&
                    texture.raw_tex_metadata.height > 0) {
                    const size_t offset = (static_cast<size_t>(y) * texture.raw_tex_metadata.width + x) * 4 + 3;
                    const int alpha = byteAt(offset);
                    return alpha >= 0 ? alpha / 255.0f : -1.0f;
                }

                switch (tile.fmt) {
                    case G_IM_FMT_RGBA:
                        if (tile.siz == G_IM_SIZ_16b) {
                            const size_t offset = rowOffset + static_cast<size_t>(x) * 2;
                            const int hi = byteAt(offset);
                            const int lo = byteAt(offset + 1);
                            if (hi < 0 || lo < 0) {
                                return -1.0f;
                            }
                            return (lo & 1) != 0 ? 1.0f : 0.0f;
                        }
                        if (tile.siz == G_IM_SIZ_32b) {
                            const int alpha = byteAt(rowOffset + static_cast<size_t>(x) * 4 + 3);
                            return alpha >= 0 ? alpha / 255.0f : -1.0f;
                        }
                        break;
                    case G_IM_FMT_IA:
                        if (tile.siz == G_IM_SIZ_4b) {
                            const int packed = byteAt(rowOffset + static_cast<size_t>(x) / 2);
                            if (packed < 0) {
                                return -1.0f;
                            }
                            const int nibble = (x & 1) == 0 ? packed >> 4 : packed & 0xF;
                            return (nibble & 1) != 0 ? 1.0f : 0.0f;
                        }
                        if (tile.siz == G_IM_SIZ_8b) {
                            const int packed = byteAt(rowOffset + x);
                            return packed >= 0 ? (packed & 0xF) / 15.0f : -1.0f;
                        }
                        if (tile.siz == G_IM_SIZ_16b) {
                            const int alpha = byteAt(rowOffset + static_cast<size_t>(x) * 2 + 1);
                            return alpha >= 0 ? alpha / 255.0f : -1.0f;
                        }
                        break;
                    case G_IM_FMT_CI:
                        if (tile.siz == G_IM_SIZ_4b) {
                            const int packed = byteAt(rowOffset + static_cast<size_t>(x) / 2);
                            if (packed < 0) {
                                return -1.0f;
                            }
                            const uint32_t index = ((x & 1) == 0 ? packed >> 4 : packed & 0xF) |
                                                   (static_cast<uint32_t>(tile.palette) << 4);
                            return paletteAlpha(index);
                        }
                        if (tile.siz == G_IM_SIZ_8b) {
                            const int index = byteAt(rowOffset + x);
                            return index >= 0 ? paletteAlpha(static_cast<uint32_t>(index)) : -1.0f;
                        }
                        break;
                    case G_IM_FMT_I:
                        return 1.0f;
                    default:
                        break;
                }
                return -1.0f;
            };

            const ShadowFoliageVertex source[3] = {
                { v1->wx, v1->wy, v1->wz, v1->u, v1->v },
                { v2->wx, v2->wy, v2->wz, v2->u, v2->v },
                { v3->wx, v3->wy, v3->wz, v3->u, v3->v },
            };

            auto interpolate = [&](float a, float b) {
                const float c = 1.0f - a - b;
                return ShadowFoliageVertex{
                    source[0].x * c + source[1].x * a + source[2].x * b,
                    source[0].y * c + source[1].y * a + source[2].y * b,
                    source[0].z * c + source[1].z * a + source[2].z * b,
                    source[0].u * c + source[1].u * a + source[2].u * b,
                    source[0].v * c + source[1].v * a + source[2].v * b,
                };
            };

            auto emitAlphaTested = [&](const ShadowFoliageVertex& a, const ShadowFoliageVertex& b,
                                       const ShadowFoliageVertex& c) {
                const float sampleU = (a.u + b.u + c.u) / 3.0f;
                const float sampleV = (a.v + b.v + c.v) / 3.0f;
                float alpha = sampleFoliageAlpha(sampleU, sampleV);
                if (alpha < 0.0f) {
                    const float hash = sinf(sampleU * 0.06711056f + sampleV * 0.00583715f) * 43758.5453f;
                    alpha = (hash - floorf(hash)) < 0.58f ? 1.0f : 0.0f;
                } else if (alpha > 0.0f && alpha < 1.0f) {
                    const float hash = sinf(sampleU * 0.06711056f + sampleV * 0.00583715f) * 43758.5453f;
                    const float threshold = hash - floorf(hash);
                    alpha = alpha >= threshold ? 1.0f : 0.0f;
                }
                if (alpha >= 0.5f) {
                    appendShadowTriangle(a.x, a.y, a.z, b.x, b.y, b.z, c.x, c.y, c.z);
                }
            };

            constexpr int kFoliageSubdivisions = 12;
            for (int i = 0; i < kFoliageSubdivisions; ++i) {
                for (int j = 0; j < kFoliageSubdivisions - i; ++j) {
                    const float inv = 1.0f / static_cast<float>(kFoliageSubdivisions);
                    const ShadowFoliageVertex p00 = interpolate(i * inv, j * inv);
                    const ShadowFoliageVertex p10 = interpolate((i + 1) * inv, j * inv);
                    const ShadowFoliageVertex p01 = interpolate(i * inv, (j + 1) * inv);
                    emitAlphaTested(p00, p10, p01);
                    if (i + j + 1 < kFoliageSubdivisions) {
                        const ShadowFoliageVertex p11 = interpolate((i + 1) * inv, (j + 1) * inv);
                        emitAlphaTested(p10, p11, p01);
                    }
                }
            }
        }
    }
'''

    if old_gated in text:
        text = replace_once(text, old_gated, new, "alpha-aware foliage capture")
    elif old_ungated in text:
        text = replace_once(text, old_ungated, new, "alpha-aware foliage capture (ungated)")
    elif "kFoliageSubdivisions" not in text:
        raise RuntimeError("alpha-aware foliage capture: source block not found")

    path.write_text(text, encoding="utf-8", newline="\n")


def patch_backend(root: Path) -> None:
    path = root / "libultraship/src/fast/backends/gfx_direct3d11_shadow_map.inc"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "const float thresholdWidth = max(fwidth(rawVisibility) * 1.5, 0.025);",
        "const float thresholdWidth = 0.015; // fixed resolve width: camera distance no longer changes coverage",
    )
    old = '''    const float shadow = 1.0 - visibility;
    float localFill = 0.0;
    if (localLight.w > 0.0) {
        localFill = saturate(1.0 - length(world.xyz - localLight.xyz) / localLight.w);
        localFill = localFill * localFill * (3.0 - 2.0 * localFill);
    }
    const float localShadowScale = 1.0 - localFill * 0.85;
    return float4(0.0, 0.0, 0.0,
                  saturate(shadowParams.x * shadow * edgeFade * localShadowScale));
'''
    new = '''    const float shadow = 1.0 - visibility;
    // Local lights now steer the shadow-map direction on the game side. They must not erase opacity
    // radially here: that produced a bright halo around Navi and made 100% opacity vary with distance.
    return float4(0.0, 0.0, 0.0, saturate(shadowParams.x * shadow * edgeFade));
'''
    count = text.count(old)
    if count == 2:
        text = text.replace(old, new)
    elif "Local lights now steer the shadow-map direction" not in text:
        raise RuntimeError(f"DX11 local-fill removal: expected 2 matches, found {count}")
    if text.count("fixed resolve width: camera distance no longer changes coverage") != 2:
        raise RuntimeError("DX11 fixed resolve threshold was not applied to both shaders")
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_toon_lighting(root: Path) -> None:
    path = root / "soh/soh/Enhancements/Graphics/ToonLighting.cpp"
    text = path.read_text(encoding="utf-8")
    old = '''    // Dynamic shadow mapping uses one stable directional light and one stable world-space anchor.
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
    }
'''
    new = '''    // Dynamic shadow mapping uses one stable world-space anchor. The environment remains the main
    // directional source, but Navi now participates as a real local key: in dark scenes the map direction
    // bends toward her actual point-light position, so silhouettes cast away from the fairy. The renderer's
    // old radial "fill" is deliberately disabled because it created a fake dark/bright ring around Navi and
    // made a 100% opacity shadow become lighter merely because the player approached it.
    if (auto interp = GetInterpreter()) {
        f32 shadowDir[3] = { 0.30f, 1.0f, 0.20f };
        f32 shadowColor[3] = { 1.0f, 1.0f, 1.0f };
        f32 shadowAnchor[3] = { 0.0f, 0.0f, 0.0f };
        const f32 noLocalFill[4] = { 0.0f, 0.0f, 0.0f, 0.0f };
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
                    EnElf* navi = (EnElf*)naviActor;
                    LightInfo* glow = &navi->lightInfoGlow;
                    LightInfo* noGlow = &navi->lightInfoNoGlow;
                    s32 glowLum = glow->params.point.color[0] + glow->params.point.color[1] +
                                  glow->params.point.color[2];
                    s32 noGlowLum = noGlow->params.point.color[0] + noGlow->params.point.color[1] +
                                    noGlow->params.point.color[2];
                    LightInfo* naviLight = (glowLum >= noGlowLum) ? glow : noGlow;

                    f32 nx = naviLight->params.point.x - shadowAnchor[0];
                    f32 ny = naviLight->params.point.y - shadowAnchor[1];
                    f32 nz = naviLight->params.point.z - shadowAnchor[2];
                    f32 naviDist = sqrtf((nx * nx) + (ny * ny) + (nz * nz));
                    if (naviDist > 0.001f) {
                        nx /= naviDist;
                        ny /= naviDist;
                        nz /= naviDist;

                        const f32 envLum = CLAMP((shadowColor[0] + shadowColor[1] + shadowColor[2]) / 3.0f,
                                                 0.0f, 1.0f);
                        const f32 naviLum = CLAMP((naviLight->params.point.color[0] +
                                                   naviLight->params.point.color[1] +
                                                   naviLight->params.point.color[2]) /
                                                      (255.0f * 3.0f),
                                                  0.0f, 1.0f);
                        const f32 darkness = CLAMP(1.0f - (envLum * 1.35f), 0.0f, 1.0f);
                        const f32 naviWeight = CLAMP((0.10f + (0.82f * darkness)) * naviLum, 0.0f, 0.92f);
                        const f32 envWeight = 1.0f - naviWeight;

                        shadowDir[0] = (shadowDir[0] * envWeight) + (nx * naviWeight);
                        shadowDir[1] = (shadowDir[1] * envWeight) + (ny * naviWeight);
                        shadowDir[2] = (shadowDir[2] * envWeight) + (nz * naviWeight);
                        f32 blendedLen = sqrtf((shadowDir[0] * shadowDir[0]) + (shadowDir[1] * shadowDir[1]) +
                                               (shadowDir[2] * shadowDir[2]));
                        if (blendedLen > 0.001f) {
                            shadowDir[0] /= blendedLen;
                            shadowDir[1] /= blendedLen;
                            shadowDir[2] /= blendedLen;
                        }
                    }
                }
            }
        }
        interp->SetDynamicShadowCaptureState(sParams.shadows, shadowDir, shadowAnchor, noLocalFill);
    }
'''
    if old in text:
        text = replace_once(text, old, new, "Navi physical shadow direction")
    elif "Navi now participates as a real local key" not in text:
        raise RuntimeError("Navi physical shadow direction: source block not found")
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    patch_interpreter(root)
    patch_backend(root)
    patch_toon_lighting(root)
    print("Applied alpha-tested foliage, stable opacity, and directional Navi shadow lighting.")


if __name__ == "__main__":
    main()
