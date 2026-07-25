from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_interpreter(root: Path) -> None:
    path = root / "libultraship/src/fast/interpreter.cpp"
    text = path.read_text(encoding="utf-8")

    function_start = text.find("void Interpreter::RenderShadowVolumes() {")
    function_end = text.find("void Interpreter::GfxDpSetGrayscaleColor", function_start)
    if function_start < 0 or function_end < 0:
        raise RuntimeError("shadow composite signaling: RenderShadowVolumes bounds not found")

    function = text[function_start:function_end]
    marker = "    // Large dynamic casters join the world cache only for this frame's weak-fill resolve."
    if marker not in function:
        marker = "    // Resolve static/world and physically large casters first."
    if marker not in function:
        raise RuntimeError("shadow composite signaling: first-layer marker not found")

    if "bool beginShadowComposite = true;" not in function:
        function = function.replace(
            marker,
            "    // The first active layer clears the dedicated composition stencil. Every later layer tests it so\n"
            "    // overlapping world/actor/Navi shadows merge at one darkness instead of multiplying black.\n"
            "    bool beginShadowComposite = true;\n\n" + marker,
            1,
        )

    # Resolve compact actor layers first. The later world layer then fills only stencil pixels that remain clear.
    # This makes a strong actor/Navi shadow win over a weak locally lifted environment shadow at an overlap.
    world_start_marker = "    // Resolve static/world and physically large casters first."
    world_end_line = "    mEnvironmentShadowCasterCache.resize(stableEnvironmentFloats);"
    world_start = function.find(world_start_marker)
    world_end = function.find(world_end_line, world_start)
    if world_start < 0 or world_end < 0:
        raise RuntimeError("shadow layer ordering: world resolve block not found")
    world_end += len(world_end_line)
    world_block = function[world_start:world_end]
    world_block = world_block.replace(
        "Resolve static/world and physically large casters first.",
        "Resolve static/world and physically large casters after compact actors.",
        1,
    )
    function = function[:world_start] + function[world_end:]

    clear_marker = "    mShadowCasterAccum.clear();"
    clear_pos = function.find(clear_marker)
    if clear_pos < 0:
        raise RuntimeError("shadow layer ordering: final actor clear marker not found")
    function = function[:clear_pos] + world_block + "\n\n" + function[clear_pos:]

    old_tail = "kDynamicShadowMapBias, kDynamicShadowMapPcfRadius);"
    call_count = function.count(old_tail)
    if call_count < 2 or call_count > 8:
        raise RuntimeError(f"shadow composite signaling: unexpected render-call count {call_count}")

    new_tail = (
        "kDynamicShadowMapBias,\n"
        "            beginShadowComposite ? -(kDynamicShadowMapPcfRadius + 1) : kDynamicShadowMapPcfRadius);\n"
        "        beginShadowComposite = false;"
    )
    function = function.replace(old_tail, new_tail)

    text = text[:function_start] + function + text[function_end:]
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_dx11_header(root: Path) -> None:
    path = root / "libultraship/include/fast/backends/gfx_direct3d_common.h"
    text = path.read_text(encoding="utf-8")

    if "mDynamicShadowCompositeStencilTexture" not in text:
        marker = '''    Microsoft::WRL::ComPtr<ID3D11SamplerState> mDynamicShadowComparisonSampler;
    DynamicShadowCB mDynamicShadowCbData{};'''
        replacement = '''    Microsoft::WRL::ComPtr<ID3D11SamplerState> mDynamicShadowComparisonSampler;
    // Dedicated screen-sized stencil used only to merge overlapping dynamic-shadow layers. It must not share
    // the scene depth resource because the resolve shader samples that resource at the same time.
    Microsoft::WRL::ComPtr<ID3D11Texture2D> mDynamicShadowCompositeStencilTexture;
    Microsoft::WRL::ComPtr<ID3D11DepthStencilView> mDynamicShadowCompositeStencilDsv;
    uint32_t mDynamicShadowCompositeWidth = 0;
    uint32_t mDynamicShadowCompositeHeight = 0;
    uint32_t mDynamicShadowCompositeSampleCount = 0;
    uint32_t mDynamicShadowCompositeSampleQuality = 0;
    DynamicShadowCB mDynamicShadowCbData{};'''
        text = replace_once(text, marker, replacement, "DX11 dedicated shadow-composition stencil members")

    path.write_text(text, encoding="utf-8", newline="\n")


def patch_dx11_backend(root: Path) -> None:
    path = root / "libultraship/src/fast/backends/gfx_direct3d11_shadow_map.inc"
    text = path.read_text(encoding="utf-8")

    signature = '''void GfxRenderingAPIDX11::RenderDynamicShadowMap(const float* worldVertices, size_t vertexCount,
                                                 const float* cameraWorldToClip, const float lightDirection[3],
                                                 const float shadowAnchor[3], const float localLight[4],
                                                 uint32_t resolution, float opacity, float bias, int pcfRadius) {'''
    if "const bool beginShadowComposite = pcfRadius < 0;" not in text:
        text = replace_once(
            text,
            signature,
            signature
            + '''
    // Interpreter encodes the first active shadow layer with a negative PCF value. Decode it here and clear
    // the dedicated composition stencil exactly once, so later layers skip pixels that are already shadowed.
    const bool beginShadowComposite = pcfRadius < 0;
    if (beginShadowComposite) {
        pcfRadius = -pcfRadius - 1;
    }''',
            "DX11 shadow composite begin signal",
        )

    old_resolve_state = '''        depthDesc.DepthEnable = FALSE;
        depthDesc.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ZERO;
        depthDesc.DepthFunc = D3D11_COMPARISON_ALWAYS;
        ThrowIfFailed(mDevice->CreateDepthStencilState(&depthDesc, mDynamicShadowResolveDepthState.GetAddressOf()));'''
    new_resolve_state = '''        depthDesc.DepthEnable = FALSE;
        depthDesc.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ZERO;
        depthDesc.DepthFunc = D3D11_COMPARISON_ALWAYS;
        // Reserve stencil bit 7 for dynamic-shadow composition. A fragment passes only while the bit is clear,
        // then REPLACE writes it. This combines overlapping shadow coverage instead of repeatedly blending black.
        depthDesc.StencilEnable = TRUE;
        depthDesc.StencilReadMask = 0x80;
        depthDesc.StencilWriteMask = 0x80;
        depthDesc.FrontFace.StencilFailOp = D3D11_STENCIL_OP_KEEP;
        depthDesc.FrontFace.StencilDepthFailOp = D3D11_STENCIL_OP_KEEP;
        depthDesc.FrontFace.StencilPassOp = D3D11_STENCIL_OP_REPLACE;
        depthDesc.FrontFace.StencilFunc = D3D11_COMPARISON_NOT_EQUAL;
        depthDesc.BackFace = depthDesc.FrontFace;
        ThrowIfFailed(mDevice->CreateDepthStencilState(&depthDesc, mDynamicShadowResolveDepthState.GetAddressOf()));'''
    text = replace_once(text, old_resolve_state, new_resolve_state, "DX11 overlap stencil state")

    old_ensure = '''    const size_t vertexBytes = vertexCount * 3 * sizeof(float);
    EnsureDynamicShadowResources(std::max<uint32_t>(64, resolution), vertexBytes);'''
    new_ensure = '''    const size_t vertexBytes = vertexCount * 3 * sizeof(float);
    EnsureDynamicShadowResources(std::max<uint32_t>(64, resolution), vertexBytes);

    // The scene depth texture is sampled by the resolve shader, so it cannot simultaneously be rebound as a
    // writable stencil DSV. Allocate a tiny-purpose screen-sized depth/stencil resource for composition only.
    D3D11_TEXTURE2D_DESC frameColorDesc = {};
    mTextures[framebuffer.texture_id].texture->GetDesc(&frameColorDesc);
    const bool rebuildCompositeStencil =
        !mDynamicShadowCompositeStencilTexture || mDynamicShadowCompositeWidth != frameColorDesc.Width ||
        mDynamicShadowCompositeHeight != frameColorDesc.Height ||
        mDynamicShadowCompositeSampleCount != frameColorDesc.SampleDesc.Count ||
        mDynamicShadowCompositeSampleQuality != frameColorDesc.SampleDesc.Quality;
    if (rebuildCompositeStencil) {
        mDynamicShadowCompositeStencilDsv.Reset();
        mDynamicShadowCompositeStencilTexture.Reset();

        D3D11_TEXTURE2D_DESC stencilTextureDesc = {};
        stencilTextureDesc.Width = frameColorDesc.Width;
        stencilTextureDesc.Height = frameColorDesc.Height;
        stencilTextureDesc.MipLevels = 1;
        stencilTextureDesc.ArraySize = 1;
        stencilTextureDesc.Format = DXGI_FORMAT_R24G8_TYPELESS;
        stencilTextureDesc.SampleDesc = frameColorDesc.SampleDesc;
        stencilTextureDesc.Usage = D3D11_USAGE_DEFAULT;
        stencilTextureDesc.BindFlags = D3D11_BIND_DEPTH_STENCIL;
        ThrowIfFailed(mDevice->CreateTexture2D(&stencilTextureDesc, nullptr,
                                               mDynamicShadowCompositeStencilTexture.GetAddressOf()));

        D3D11_DEPTH_STENCIL_VIEW_DESC stencilViewDesc = {};
        stencilViewDesc.Format = DXGI_FORMAT_D24_UNORM_S8_UINT;
        stencilViewDesc.ViewDimension = frameColorDesc.SampleDesc.Count > 1
                                            ? D3D11_DSV_DIMENSION_TEXTURE2DMS
                                            : D3D11_DSV_DIMENSION_TEXTURE2D;
        if (frameColorDesc.SampleDesc.Count == 1) {
            stencilViewDesc.Texture2D.MipSlice = 0;
        }
        ThrowIfFailed(mDevice->CreateDepthStencilView(mDynamicShadowCompositeStencilTexture.Get(), &stencilViewDesc,
                                                      mDynamicShadowCompositeStencilDsv.GetAddressOf()));

        mDynamicShadowCompositeWidth = frameColorDesc.Width;
        mDynamicShadowCompositeHeight = frameColorDesc.Height;
        mDynamicShadowCompositeSampleCount = frameColorDesc.SampleDesc.Count;
        mDynamicShadowCompositeSampleQuality = frameColorDesc.SampleDesc.Quality;
    }'''
    text = replace_once(text, old_ensure, new_ensure, "DX11 dedicated composition stencil allocation")

    old_saved_targets = '''    ComPtr<ID3D11RenderTargetView> savedRtv;
    ComPtr<ID3D11DepthStencilView> savedDsv;
    mContext->OMGetRenderTargets(1, savedRtv.GetAddressOf(), savedDsv.GetAddressOf());'''
    new_saved_targets = '''    ComPtr<ID3D11RenderTargetView> savedRtv;
    ComPtr<ID3D11DepthStencilView> savedDsv;
    mContext->OMGetRenderTargets(1, savedRtv.GetAddressOf(), savedDsv.GetAddressOf());
    if (beginShadowComposite && mDynamicShadowCompositeStencilDsv) {
        mContext->ClearDepthStencilView(mDynamicShadowCompositeStencilDsv.Get(), D3D11_CLEAR_STENCIL, 1.0f, 0);
    }'''
    text = replace_once(text, old_saved_targets, new_saved_targets, "DX11 dedicated stencil clear")

    text = replace_once(
        text,
        "    mContext->OMSetRenderTargets(1, &resolveRtv, nullptr);",
        "    mContext->OMSetRenderTargets(1, &resolveRtv, mDynamicShadowCompositeStencilDsv.Get());",
        "DX11 bind dedicated composition stencil",
    )
    text = replace_once(
        text,
        "    mContext->OMSetDepthStencilState(mDynamicShadowResolveDepthState.Get(), 0);",
        "    mContext->OMSetDepthStencilState(mDynamicShadowResolveDepthState.Get(), 0x80);",
        "DX11 reserved shadow stencil reference",
    )

    old_shader = '''    const bool casterReceiverOnly = localLight.w <= -4096.0;
    if (casterReceiverOnly && world.y > localLight.x) discard;

    float localFill = 0.0;
    const float localRadius = casterReceiverOnly ? 0.0 : abs(localLight.w);
    if (localRadius > 0.0) {
        localFill = saturate(1.0 - length(world.xyz - localLight.xyz) / localRadius);
        localFill = localFill * localFill * (3.0 - 2.0 * localFill);
    }
    const float localFillStrength = (!casterReceiverOnly && localLight.w < 0.0) ? 0.35 : 0.85;
    const float localShadowScale = 1.0 - localFill * localFillStrength;
    return float4(0.0, 0.0, 0.0,
                  saturate(shadowParams.x * shadow * edgeFade * localShadowScale));'''
    new_shader = '''    const bool casterReceiverOnly = localLight.w <= -4096.0;
    float receiverFade = 1.0;
    if (casterReceiverOnly) {
        // Keep the caster body excluded, but fade the cutoff over a short vertical band so a platform edge cannot
        // reveal a hard rectangular boundary in the projected actor shadow.
        const float receiverFadeHeight = 12.0;
        receiverFade = saturate((localLight.x + receiverFadeHeight - world.y) / receiverFadeHeight);
        if (receiverFade <= 0.001) discard;
    }

    float localFill = 0.0;
    const float localRadius = casterReceiverOnly ? 0.0 : abs(localLight.w);
    if (localRadius > 0.0) {
        localFill = saturate(1.0 - length(world.xyz - localLight.xyz) / localRadius);
        localFill = localFill * localFill * (3.0 - 2.0 * localFill);
    }
    const float localFillStrength = (!casterReceiverOnly && localLight.w < 0.0) ? 0.35 : 0.85;
    const float localShadowScale = 1.0 - localFill * localFillStrength;
    const float resolvedShadowAlpha =
        saturate(shadowParams.x * shadow * edgeFade * localShadowScale * receiverFade);
    // Zero-alpha fragments must be discarded; otherwise they would still write stencil and incorrectly block a
    // later layer that contains a real shadow at the same pixel.
    if (resolvedShadowAlpha <= 0.01) discard;
    return float4(0.0, 0.0, 0.0, resolvedShadowAlpha);'''
    shader_count = text.count(old_shader)
    if shader_count != 2:
        raise RuntimeError(f"DX11 merged shadow shader: expected two matches, found {shader_count}")
    text = text.replace(old_shader, new_shader)

    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    patch_interpreter(root)
    patch_dx11_header(root)
    patch_dx11_backend(root)
    print(
        "Merged overlapping dynamic-shadow layers through a dedicated stencil and softened the receiver cutoff."
    )


if __name__ == "__main__":
    main()
