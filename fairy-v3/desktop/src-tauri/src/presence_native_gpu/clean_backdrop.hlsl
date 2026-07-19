Texture2D<float4> captured_texture : register(t0);
Texture2D<float4> previous_clean_texture : register(t1);
Texture2D<float4> previous_overlay_texture : register(t2);
SamplerState backdrop_sampler : register(s0);

cbuffer CleanBackdropConstants : register(b0) {
    float2 capture_size;
    float2 overlay_origin_px;
    float2 overlay_size_px;
    float2 input_origin_px;
    float2 input_size_px;
    float previous_clean_valid;
    float previous_overlay_valid;
    float capture_linear;
    float input_surface_valid;
    float drag_active;
    float constants_padding;
};

struct VertexOutput {
    float4 position : SV_Position;
    float2 uv : TEXCOORD0;
};

VertexOutput clean_vs_main(uint vertex_id : SV_VertexID) {
    VertexOutput output;
    float2 position = float2((vertex_id << 1) & 2, vertex_id & 2);
    output.uv = position;
    output.position = float4(position * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);
    return output;
}

float inside_rectangle(float2 point_px, float2 origin_px, float2 size_px) {
    float2 lower = step(origin_px, point_px);
    float2 upper = step(point_px, origin_px + max(size_px - 1.0.xx, 0.0.xx));
    return lower.x * lower.y * upper.x * upper.y;
}

float3 srgb_to_linear(float3 color) {
    float3 low = color / 12.92;
    float3 high = pow((color + 0.055) / 1.055, 2.4);
    return lerp(low, high, step(0.04045.xxx, color));
}

float3 linear_to_srgb(float3 color) {
    color = max(color, 0.0.xxx);
    float3 low = color * 12.92;
    float3 high = 1.055 * pow(color, 1.0 / 2.4) - 0.055;
    return lerp(low, high, step(0.0031308.xxx, color));
}

float max_component(float3 value) {
    return max(value.r, max(value.g, value.b));
}

float3 recover_composited_backdrop(
    float3 captured_value,
    float4 overlay,
    float3 previous_clean_value,
    float2 capture_uv,
    float2 overlay_uv
) {
    float alpha = saturate(overlay.a);
    float3 captured_linear = capture_linear > 0.5
        ? max(captured_value, 0.0.xxx)
        : srgb_to_linear(saturate(captured_value));
    float3 previous_clean_linear = capture_linear > 0.5
        ? max(previous_clean_value, 0.0.xxx)
        : srgb_to_linear(saturate(previous_clean_value));

    // The swap-chain stores premultiplied encoded RGB. Convert it back to straight color before
    // entering the compositor's linear working space, then remove the known previous overlay.
    float3 overlay_straight = saturate(overlay.rgb / max(alpha, 0.001));
    float3 overlay_linear = srgb_to_linear(overlay_straight) * alpha;
    float denominator = max(1.0 - alpha, 0.06);
    float3 recovered_linear = (captured_linear - overlay_linear) / denominator;

    float3 below_gamut = max(-recovered_linear, 0.0.xxx);
    float3 above_gamut = max(recovered_linear - 1.0.xxx, 0.0.xxx);
    float gamut_error = max(max_component(below_gamut), max_component(above_gamut));

    // Sub-pixel movement and capture timestamps disagree most often on a sharp coverage edge.
    // Recover those pixels from two physical pixels toward the lower-coverage side. That neighbor
    // contains the same monitor composition without the bright optical boundary, so it updates
    // moving content instead of retaining a stale colored ring forever.
    float2 alpha_gradient_vector = float2(ddx(alpha), ddy(alpha));
    float alpha_gradient = length(alpha_gradient_vector);
    float neighbor_weight = 0.0;
    float candidate_alpha = alpha;
    if (alpha_gradient > 0.018) {
        float2 offset_px = -alpha_gradient_vector / max(alpha_gradient, 0.0001) * 2.0;
        float2 neighbor_capture_uv = saturate(capture_uv + offset_px / capture_size);
        float2 neighbor_overlay_uv = saturate(overlay_uv + offset_px / max(overlay_size_px, 1.0.xx));
        float3 neighbor_captured = captured_texture.Sample(
            backdrop_sampler,
            neighbor_capture_uv
        ).rgb;
        float4 neighbor_overlay = previous_overlay_texture.Sample(
            backdrop_sampler,
            neighbor_overlay_uv
        );
        float neighbor_alpha = saturate(neighbor_overlay.a);
        float3 neighbor_captured_linear = capture_linear > 0.5
            ? max(neighbor_captured, 0.0.xxx)
            : srgb_to_linear(saturate(neighbor_captured));
        float3 neighbor_straight = saturate(
            neighbor_overlay.rgb / max(neighbor_alpha, 0.001)
        );
        float3 neighbor_overlay_linear = srgb_to_linear(neighbor_straight) * neighbor_alpha;
        float3 neighbor_recovered = (
            neighbor_captured_linear - neighbor_overlay_linear
        ) / max(1.0 - neighbor_alpha, 0.06);
        float3 neighbor_below = max(-neighbor_recovered, 0.0.xxx);
        float3 neighbor_above = max(neighbor_recovered - 1.0.xxx, 0.0.xxx);
        float neighbor_gamut_error = max(
            max_component(neighbor_below),
            max_component(neighbor_above)
        );
        float lower_coverage = smoothstep(0.008, 0.075, alpha - neighbor_alpha);
        float neighbor_gamut_confidence = 1.0 - smoothstep(
            0.015,
            0.11,
            neighbor_gamut_error
        );
        neighbor_weight = saturate(lower_coverage * neighbor_gamut_confidence);
        recovered_linear = lerp(recovered_linear, neighbor_recovered, neighbor_weight);
        gamut_error = lerp(gamut_error, neighbor_gamut_error, neighbor_weight);
        candidate_alpha = lerp(alpha, neighbor_alpha, neighbor_weight);
    }

    float gamut_confidence = 1.0 - smoothstep(0.015, 0.11, gamut_error);
    float direct_edge_confidence = 1.0 - smoothstep(0.018, 0.105, alpha_gradient);
    float edge_confidence = lerp(direct_edge_confidence, 1.0, neighbor_weight);
    float alpha_confidence = 1.0 - smoothstep(0.88, 0.945, candidate_alpha);
    float confidence = saturate(gamut_confidence * edge_confidence * alpha_confidence);

    // High-coverage pixels have little numerical headroom. Bound their per-frame change while
    // still allowing a moving desktop to converge continuously when Fairy is stationary.
    float max_step = lerp(0.34, 0.065, smoothstep(0.18, 0.94, alpha));
    float3 limited = clamp(
        saturate(recovered_linear),
        previous_clean_linear - max_step,
        previous_clean_linear + max_step
    );
    float3 clean_linear = lerp(previous_clean_linear, limited, confidence);
    return capture_linear > 0.5 ? clean_linear : linear_to_srgb(clean_linear);
}

float4 clean_ps_main(VertexOutput input) : SV_Target {
    float2 pixel = input.uv * capture_size;
    float4 captured = captured_texture.Sample(backdrop_sampler, input.uv);
    if (previous_clean_valid < 0.5) {
        return float4(captured.rgb, 1.0);
    }

    float3 previous_clean = previous_clean_texture.Sample(backdrop_sampler, input.uv).rgb;
    if (
        input_surface_valid > 0.5 &&
        inside_rectangle(pixel, input_origin_px, input_size_px) > 0.5
    ) {
        // DOM text and controls are not part of the optical material. Keep them out of the clean
        // cache so they can never become recursive content inside the native glass surface.
        return float4(previous_clean, 1.0);
    }
    if (
        previous_overlay_valid < 0.5 ||
        inside_rectangle(pixel, overlay_origin_px, overlay_size_px) < 0.5
    ) {
        return float4(captured.rgb, 1.0);
    }

    float2 overlay_uv = saturate((pixel - overlay_origin_px) / max(overlay_size_px, 1.0.xx));
    float4 overlay = previous_overlay_texture.Sample(backdrop_sampler, overlay_uv);
    if (overlay.a <= 0.002) {
        return float4(captured.rgb, 1.0);
    }

    return float4(
        recover_composited_backdrop(
            captured.rgb,
            overlay,
            previous_clean,
            input.uv,
            overlay_uv
        ),
        1.0
    );
}
