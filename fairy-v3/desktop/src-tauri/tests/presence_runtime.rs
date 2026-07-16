use fairy_desktop_v3::presence_runtime::resolve_frame_rate_limit;

#[test]
fn power_saver_or_fullscreen_caps_presence_at_fifteen_fps() {
    assert_eq!(resolve_frame_rate_limit(false, false), 144);
    assert_eq!(resolve_frame_rate_limit(true, false), 15);
    assert_eq!(resolve_frame_rate_limit(false, true), 15);
    assert_eq!(resolve_frame_rate_limit(true, true), 15);
}
