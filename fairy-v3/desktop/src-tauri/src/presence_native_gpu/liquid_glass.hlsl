Texture2D<float4> desktop_texture : register(t0);
SamplerState desktop_sampler : register(s0);

cbuffer PresenceConstants : register(b0) {
    float2 output_size;
    float2 capture_size;
    float2 source_origin_px;
    float elapsed_seconds;
    float surface_scale;
    float capsule_visible;
    float expansion_direction;
    float visual_state;
    float material_opacity;
    float4 reserved;
};

struct VertexOutput {
    float4 position : SV_Position;
    float2 uv : TEXCOORD0;
};

VertexOutput vs_main(uint vertex_id : SV_VertexID) {
    VertexOutput output;
    float2 position = float2((vertex_id << 1) & 2, vertex_id & 2);
    output.uv = position;
    output.position = float4(position * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);
    return output;
}

float circle_sdf(float2 position_px, float radius) {
    return length(position_px) - radius;
}

float rounded_box_sdf(float2 position_px, float2 half_extent, float radius) {
    float2 q = abs(position_px) - half_extent + radius;
    return min(max(q.x, q.y), 0.0) + length(max(q, 0.0)) - radius;
}

float smooth_union(float a, float b, float radius) {
    float h = saturate(0.5 + 0.5 * (b - a) / radius);
    return lerp(b, a, h) - radius * h * (1.0 - h);
}

float direction_value() {
    return expansion_direction < 0.0 ? -1.0 : 1.0;
}

float2 core_center_px() {
    return float2(direction_value() > 0.0 ? 96.0 : 544.0, 130.0) * surface_scale;
}

float2 capsule_center_px() {
    return float2(direction_value() > 0.0 ? 386.0 : 254.0, 130.0) * surface_scale;
}

float core_sdf(float2 position_px) {
    return circle_sdf(position_px - core_center_px(), 67.0 * surface_scale);
}

float capsule_sdf(float2 position_px) {
    return rounded_box_sdf(
        position_px - capsule_center_px(),
        float2(230.0, 28.0) * surface_scale,
        27.0 * surface_scale
    );
}

float scene_sdf(float2 position_px) {
    float direction = direction_value();
    float core = core_sdf(position_px);
    if (capsule_visible < 0.5) {
        return core;
    }
    float capsule = capsule_sdf(position_px);
    float bridge_center_x = direction > 0.0 ? 158.0 : 482.0;
    float bridge = rounded_box_sdf(
        position_px - float2(bridge_center_x, 130.0) * surface_scale,
        float2(42.0, 15.0) * surface_scale,
        14.0 * surface_scale
    );
    return smooth_union(smooth_union(core, bridge, 14.0 * surface_scale), capsule, 10.0 * surface_scale);
}

float thickness_field(float signed_distance) {
    float inside = saturate(-signed_distance / max(54.0 * surface_scale, 1.0));
    float edge = exp(-abs(signed_distance) / max(7.0 * surface_scale, 1.0));
    return saturate(inside * 0.58 + edge);
}

float2 surface_normal(float2 position_px) {
    float epsilon = max(1.0, surface_scale);
    float2 gradient = float2(
        scene_sdf(position_px + float2(epsilon, 0.0)) - scene_sdf(position_px - float2(epsilon, 0.0)),
        scene_sdf(position_px + float2(0.0, epsilon)) - scene_sdf(position_px - float2(0.0, epsilon))
    );
    return normalize(gradient + float2(0.0001, 0.0001));
}

float2 lens_warp_px(float2 position_px, float signed_distance, float2 normal) {
    float scale = max(surface_scale, 0.01);
    float2 core_delta = position_px - core_center_px();
    float core_radius = saturate(length(core_delta) / (67.0 * scale));
    float2 core_warp = -core_delta * (0.24 * (1.0 - core_radius * core_radius));

    float2 capsule_delta = position_px - capsule_center_px();
    float2 capsule_extent = float2(230.0, 28.0) * scale;
    float2 capsule_ratio = abs(capsule_delta) / max(capsule_extent, 1.0);
    float capsule_depth = saturate(1.0 - max(capsule_ratio.x, capsule_ratio.y));
    float2 capsule_warp = -capsule_delta * float2(0.045, 0.34) * (0.45 + 0.55 * capsule_depth);

    float core_distance = core_sdf(position_px);
    float capsule_distance = capsule_visible > 0.5 ? capsule_sdf(position_px) : 10000.0;
    float core_weight = smoothstep(-10.0 * scale, 10.0 * scale, capsule_distance - core_distance);
    float edge_lens = exp(-abs(signed_distance) / max(9.0 * scale, 1.0));
    return lerp(capsule_warp, core_warp, core_weight) + normal * edge_lens * 9.0 * scale;
}

float state_energy_value() {
    if (visual_state == 2.0 || visual_state == 3.0) return 0.16;
    if (visual_state == 4.0 || visual_state == 5.0) return 0.22;
    if (visual_state == 6.0 || visual_state == 8.0) return 0.18;
    return 0.10;
}

float4 ps_main(VertexOutput input) : SV_Target {
    if (reserved.x > 0.5) {
        return float4(1.0, 0.0, 1.0, 1.0);
    }
    float2 local_px = input.uv * output_size;
    float signed_distance = scene_sdf(local_px);
    float scale = max(surface_scale, 0.01);
    if (signed_distance > 11.0 * scale) {
        return float4(0.0, 0.0, 0.0, 0.0);
    }

    float outside_shadow = exp(-max(signed_distance, 0.0) / max(3.8 * scale, 1.0))
        * saturate(signed_distance / max(1.5 * scale, 1.0))
        * 0.16 * material_opacity;
    if (signed_distance > 2.0 * scale) {
        return float4(0.0, 0.0, 0.0, outside_shadow);
    }

    float thickness = thickness_field(signed_distance);
    float2 normal = surface_normal(local_px);
    float2 base_uv = (source_origin_px + local_px) / capture_size;
    float edge_focus = exp(-abs(signed_distance) / max(5.2 * scale, 1.0));
    float2 warp_uv = lens_warp_px(local_px, signed_distance, normal) / capture_size;
    float dispersion_px = (0.8 + 2.6 * edge_focus) * scale;
    float2 dispersion_uv = normal * dispersion_px / capture_size;
    float2 tangent_uv = float2(-normal.y, normal.x) * (0.75 * edge_focus * scale) / capture_size;

    float red = desktop_texture.Sample(desktop_sampler, base_uv + warp_uv + dispersion_uv).r;
    float green = desktop_texture.Sample(desktop_sampler, base_uv + warp_uv).g;
    float blue = desktop_texture.Sample(desktop_sampler, base_uv + warp_uv - dispersion_uv).b;
    float3 refracted = float3(red, green, blue);
    float3 edge_blur = 0.5 * (
        desktop_texture.Sample(desktop_sampler, base_uv + warp_uv + tangent_uv).rgb
        + desktop_texture.Sample(desktop_sampler, base_uv + warp_uv - tangent_uv).rgb
    );
    refracted = lerp(refracted, edge_blur, 0.16 * edge_focus);

    float3 view_vector = normalize(float3(
        (local_px - output_size * 0.5) / max(output_size.y, 1.0),
        1.35
    ));
    float3 surface_vector = normalize(float3(normal * thickness, 1.0 - thickness * 0.72));
    float fresnel = pow(1.0 - saturate(dot(view_vector, surface_vector)), 4.2);
    float edge_band = exp(-abs(signed_distance) / max(3.0 * scale, 1.0));
    float highlight_primary = pow(saturate(dot(normalize(float2(-0.74, -0.67)), -normal)), 14.0) * edge_band;
    float highlight_secondary = pow(saturate(dot(normalize(float2(0.66, 0.75)), -normal)), 24.0) * edge_band;
    float outer_caustic = exp(-abs(signed_distance + 7.0 * scale) / max(3.2 * scale, 1.0));
    float inner_caustic = exp(-abs(signed_distance + 20.0 * scale) / max(6.0 * scale, 1.0));
    float bottom_shadow = pow(saturate(normal.y), 6.0) * edge_band;

    float state_energy = state_energy_value();
    float luminance = dot(refracted, float3(0.2126, 0.7152, 0.0722));
    float adaptive_tint = lerp(0.075, 0.035, saturate(luminance));
    float3 color = lerp(refracted, float3(0.78, 0.92, 1.0), adaptive_tint + 0.035 * edge_focus);
    color += float3(1.0, 0.99, 0.97) * highlight_primary * 0.52;
    color += float3(0.68, 0.88, 1.0) * highlight_secondary * 0.34;
    color += float3(0.72, 0.95, 1.0) * outer_caustic * 0.22;
    color += float3(0.56, 0.84, 1.0) * inner_caustic * 0.10;
    color += float3(0.58, 0.86, 1.0) * fresnel * edge_band * 0.22;
    color *= 1.0 - bottom_shadow * 0.22;

    float core_radius = length(local_px - core_center_px()) / scale;
    float pulse = 0.82 + 0.18 * sin(elapsed_seconds * 2.4);
    float core_ring_outer = exp(-abs(core_radius - 40.0) / 2.2);
    float core_ring_inner = exp(-abs(core_radius - 24.0) / 2.0);
    float core_orb = exp(-(core_radius * core_radius) / 150.0);
    float3 core_light = float3(0.34, 0.78, 1.0) * (core_ring_outer * 0.62 + core_ring_inner * 0.32);
    core_light += float3(0.90, 0.98, 1.0) * core_orb * (0.35 + state_energy) * pulse;
    color += core_light;

    float shape_mask = saturate((2.0 * scale - signed_distance) / (3.0 * scale));
    float center_alpha = 0.38 * material_opacity;
    float edge_alpha = (0.64 + fresnel * 0.16 + outer_caustic * 0.08) * material_opacity;
    float alpha = shape_mask * lerp(center_alpha, edge_alpha, saturate(edge_focus + thickness * 0.22));
    alpha = saturate(alpha + core_orb * 0.05 * material_opacity);
    return float4(saturate(color) * alpha, alpha);
}
