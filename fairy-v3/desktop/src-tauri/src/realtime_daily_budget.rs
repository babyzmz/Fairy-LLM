use serde_json::Value;
#[cfg(not(windows))]
use std::time::{SystemTime, UNIX_EPOCH};

#[cfg(not(windows))]
const DAY_MS: u64 = 24 * 60 * 60 * 1_000;
const MIN_LOCAL_DAY_MS: u64 = 22 * 60 * 60 * 1_000;
const MAX_LOCAL_DAY_MS: u64 = 26 * 60 * 60 * 1_000;
#[cfg(windows)]
const WINDOWS_TO_UNIX_EPOCH_100NS: u64 = 116_444_736_000_000_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RealtimeUtcDayBounds {
    pub start_ms: u64,
    pub end_ms: u64,
}

impl RealtimeUtcDayBounds {
    fn new(start_ms: u64, end_ms: u64) -> Result<Self, &'static str> {
        let duration = end_ms
            .checked_sub(start_ms)
            .ok_or("REALTIME_CLOUD_DAY_BOUNDS_UNAVAILABLE")?;
        if !(MIN_LOCAL_DAY_MS..=MAX_LOCAL_DAY_MS).contains(&duration) {
            return Err("REALTIME_CLOUD_DAY_BOUNDS_UNAVAILABLE");
        }
        Ok(Self { start_ms, end_ms })
    }
}

pub fn enforce_daily_limit(used_ms: u64, limit_minutes: u16) -> Result<(), &'static str> {
    let limit_ms = u64::from(limit_minutes) * 60_000;
    if used_ms >= limit_ms {
        Err("REALTIME_CLOUD_DAILY_LIMIT_REACHED")
    } else {
        Ok(())
    }
}

pub fn parse_cloud_usage_response(response: &Value) -> Result<u64, &'static str> {
    response
        .get("result")
        .and_then(|result| result.get("wall_time_ms"))
        .and_then(Value::as_u64)
        .ok_or("REALTIME_CLOUD_USAGE_UNAVAILABLE")
}

#[cfg(windows)]
pub fn current_local_day_utc_bounds() -> Result<RealtimeUtcDayBounds, &'static str> {
    use windows_sys::Win32::Foundation::SYSTEMTIME;
    use windows_sys::Win32::System::SystemInformation::GetLocalTime;

    let mut local_now = SYSTEMTIME::default();
    // SAFETY: GetLocalTime initializes the caller-owned SYSTEMTIME.
    unsafe { GetLocalTime(&mut local_now) };
    let (next_year, next_month, next_day) =
        next_calendar_date(local_now.wYear, local_now.wMonth, local_now.wDay)?;
    let start = SYSTEMTIME {
        wYear: local_now.wYear,
        wMonth: local_now.wMonth,
        wDay: local_now.wDay,
        ..SYSTEMTIME::default()
    };
    let end = SYSTEMTIME {
        wYear: next_year,
        wMonth: next_month,
        wDay: next_day,
        ..SYSTEMTIME::default()
    };
    RealtimeUtcDayBounds::new(
        local_system_time_to_unix_ms(&start)?,
        local_system_time_to_unix_ms(&end)?,
    )
}

#[cfg(windows)]
fn local_system_time_to_unix_ms(
    local: &windows_sys::Win32::Foundation::SYSTEMTIME,
) -> Result<u64, &'static str> {
    use std::ptr;
    use windows_sys::Win32::Foundation::{FILETIME, SYSTEMTIME};
    use windows_sys::Win32::System::Time::{
        SystemTimeToFileTime, TzSpecificLocalTimeToSystemTimeEx,
    };

    let mut utc = SYSTEMTIME::default();
    // SAFETY: all pointers reference initialized caller-owned structures. A
    // null timezone pointer selects the device's current dynamic timezone.
    if unsafe { TzSpecificLocalTimeToSystemTimeEx(ptr::null(), local, &mut utc) } == 0 {
        return Err("REALTIME_CLOUD_DAY_BOUNDS_UNAVAILABLE");
    }
    let mut file_time = FILETIME::default();
    // SAFETY: both pointers reference valid structures for the duration of the call.
    if unsafe { SystemTimeToFileTime(&utc, &mut file_time) } == 0 {
        return Err("REALTIME_CLOUD_DAY_BOUNDS_UNAVAILABLE");
    }
    file_time_to_unix_ms(file_time.dwLowDateTime, file_time.dwHighDateTime)
}

#[cfg(windows)]
fn file_time_to_unix_ms(low: u32, high: u32) -> Result<u64, &'static str> {
    let ticks = (u64::from(high) << 32) | u64::from(low);
    ticks
        .checked_sub(WINDOWS_TO_UNIX_EPOCH_100NS)
        .map(|unix_ticks| unix_ticks / 10_000)
        .ok_or("REALTIME_CLOUD_DAY_BOUNDS_UNAVAILABLE")
}

#[cfg(not(windows))]
pub fn current_local_day_utc_bounds() -> Result<RealtimeUtcDayBounds, &'static str> {
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "REALTIME_CLOUD_DAY_BOUNDS_UNAVAILABLE")?
        .as_millis()
        .try_into()
        .map_err(|_| "REALTIME_CLOUD_DAY_BOUNDS_UNAVAILABLE")?;
    let start_ms = now_ms - (now_ms % DAY_MS);
    RealtimeUtcDayBounds::new(start_ms, start_ms + DAY_MS)
}

#[cfg(any(windows, test))]
fn next_calendar_date(year: u16, month: u16, day: u16) -> Result<(u16, u16, u16), &'static str> {
    let maximum = days_in_month(year, month).ok_or("REALTIME_CLOUD_DAY_BOUNDS_UNAVAILABLE")?;
    if day == 0 || day > maximum {
        return Err("REALTIME_CLOUD_DAY_BOUNDS_UNAVAILABLE");
    }
    if day < maximum {
        return Ok((year, month, day + 1));
    }
    if month < 12 {
        return Ok((year, month + 1, 1));
    }
    let next_year = year
        .checked_add(1)
        .ok_or("REALTIME_CLOUD_DAY_BOUNDS_UNAVAILABLE")?;
    Ok((next_year, 1, 1))
}

#[cfg(any(windows, test))]
fn days_in_month(year: u16, month: u16) -> Option<u16> {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => Some(31),
        4 | 6 | 9 | 11 => Some(30),
        2 if is_leap_year(year) => Some(29),
        2 => Some(28),
        _ => None,
    }
}

#[cfg(any(windows, test))]
fn is_leap_year(year: u16) -> bool {
    year.is_multiple_of(4) && (!year.is_multiple_of(100) || year.is_multiple_of(400))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn calendar_rollover_handles_leap_month_and_year_boundaries() {
        assert_eq!(next_calendar_date(2028, 2, 28), Ok((2028, 2, 29)));
        assert_eq!(next_calendar_date(2028, 2, 29), Ok((2028, 3, 1)));
        assert_eq!(next_calendar_date(2100, 2, 28), Ok((2100, 3, 1)));
        assert_eq!(next_calendar_date(2026, 12, 31), Ok((2027, 1, 1)));
        assert!(next_calendar_date(2026, 2, 29).is_err());
    }

    #[test]
    fn local_day_bounds_accept_dst_days_and_reject_broad_windows() {
        for duration_hours in [23_u64, 24, 25] {
            assert_eq!(
                RealtimeUtcDayBounds::new(1_000, 1_000 + duration_hours * 60 * 60 * 1_000)
                    .expect("valid local day")
                    .end_ms,
                1_000 + duration_hours * 60 * 60 * 1_000
            );
        }
        assert!(RealtimeUtcDayBounds::new(0, 21 * 60 * 60 * 1_000).is_err());
        assert!(RealtimeUtcDayBounds::new(0, 27 * 60 * 60 * 1_000).is_err());
    }

    #[test]
    fn daily_limit_rejects_the_exact_limit_without_rounding() {
        assert!(enforce_daily_limit(59_999, 1).is_ok());
        assert_eq!(
            enforce_daily_limit(60_000, 1),
            Err("REALTIME_CLOUD_DAILY_LIMIT_REACHED")
        );
        assert_eq!(
            enforce_daily_limit(60_001, 1),
            Err("REALTIME_CLOUD_DAILY_LIMIT_REACHED")
        );
    }

    #[test]
    fn cloud_usage_response_fails_closed_on_core_error_or_invalid_shape() {
        assert_eq!(
            parse_cloud_usage_response(&serde_json::json!({
                "result": {"wall_time_ms": 42}
            })),
            Ok(42)
        );
        assert_eq!(
            parse_cloud_usage_response(&serde_json::json!({
                "error": {"data": {"error_code": "WORKER_INTERRUPTED"}}
            })),
            Err("REALTIME_CLOUD_USAGE_UNAVAILABLE")
        );
        assert_eq!(
            parse_cloud_usage_response(&serde_json::json!({
                "result": {"wall_time_ms": -1}
            })),
            Err("REALTIME_CLOUD_USAGE_UNAVAILABLE")
        );
    }

    #[cfg(windows)]
    #[test]
    fn windows_file_time_epoch_converts_without_signed_wraparound() {
        let ticks = WINDOWS_TO_UNIX_EPOCH_100NS + 12_345 * 10_000;
        assert_eq!(
            file_time_to_unix_ms(ticks as u32, (ticks >> 32) as u32),
            Ok(12_345)
        );
        assert!(file_time_to_unix_ms(0, 0).is_err());
    }
}
