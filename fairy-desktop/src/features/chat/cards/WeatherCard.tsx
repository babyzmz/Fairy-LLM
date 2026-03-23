import { AssetResolver } from "../../../lib/assets/assetResolver";
import type { WeatherCardEnvelope } from "../../../lib/types/api";

interface WeatherCardProps {
  card: WeatherCardEnvelope;
}

export function WeatherCard({ card }: WeatherCardProps): JSX.Element {
  const data = card.data;
  const iconUrl = AssetResolver.resolveWeatherIcon(data.icon_key, data.icon_path);
  const location = [data.city, data.country].filter(Boolean).join(", ") || data.location || "Weather";
  const temperature = data.temperature_c ?? data.temp ?? "--";
  const high = data.high_c ?? data.high;
  const low = data.low_c ?? data.low;
  const feelsLike = data.feels_like_c ?? data.feels_like;
  const wind = data.wind_kmh ?? data.wind;

  return (
    <article className="card card--weather">
      <div className="card__header">
        <div>
          <h3>{location}</h3>
          <p>{data.condition || data.condition_key || "Current conditions"}</p>
        </div>
        {iconUrl ? <img className="weather-card__icon" src={iconUrl} alt="" /> : null}
      </div>
      <div className="weather-card__temp">{temperature}°C</div>
      <dl className="card__meta-grid">
        {high !== undefined || low !== undefined ? (
          <>
            <dt>High / Low</dt>
            <dd>
              {high ?? "--"}° / {low ?? "--"}°
            </dd>
          </>
        ) : null}
        {feelsLike !== undefined ? (
          <>
            <dt>Feels like</dt>
            <dd>{feelsLike}°C</dd>
          </>
        ) : null}
        {wind !== undefined ? (
          <>
            <dt>Wind</dt>
            <dd>{wind} km/h</dd>
          </>
        ) : null}
      </dl>
    </article>
  );
}
