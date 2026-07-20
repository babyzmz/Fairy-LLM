use fairy_desktop_v3::presence_runtime::resolve_frame_rate_limit;

#[test]
fn only_power_saver_caps_presence_at_fifteen_fps() {
    assert_eq!(resolve_frame_rate_limit(false, false), 300);
    assert_eq!(resolve_frame_rate_limit(true, false), 15);
    assert_eq!(resolve_frame_rate_limit(false, true), 300);
    assert_eq!(resolve_frame_rate_limit(true, true), 15);
}
