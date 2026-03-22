import os
import json
import requests
from flask import Flask, request, jsonify
from twilio.rest import Client as TwilioClient

app = Flask(__name__)

WMO_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "icy fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    61: "light rain", 63: "moderate rain", 65: "heavy rain",
    71: "light snow", 73: "moderate snow", 75: "heavy snow",
    80: "light showers", 81: "moderate showers", 82: "violent showers",
    95: "thunderstorm", 96: "thunderstorm w/ hail", 99: "thunderstorm w/ heavy hail",
}


def reverse_geocode(lat, lon):
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json"},
            headers={"User-Agent": "weather-agent/1.0"},
            timeout=5,
        ).json()
        addr = resp.get("address", {})
        return addr.get("city") or addr.get("town") or addr.get("village") or "your area"
    except Exception:
        return "your area"


def get_weather(lat, lon):
    result = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "daily": [
                "temperature_2m_max", "temperature_2m_min",
                "precipitation_probability_max", "precipitation_hours", "weather_code",
            ],
            "hourly": ["precipitation_probability"],
            "current": ["temperature_2m", "apparent_temperature"],
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "forecast_days": 1,
        },
        timeout=5,
    ).json()
    print(f"Open-Meteo response: {result}")
    return result


def find_rain_window(weather):
    hours = weather["hourly"]["time"]
    probs = weather["hourly"]["precipitation_probability"]
    rain_hours = [
        h for h, p in zip(hours, probs)
        if p >= 40 and 6 <= int(h.split("T")[1][:2]) <= 22
    ]
    if not rain_hours:
        return None
    start = rain_hours[0].split("T")[1][:5]
    end = rain_hours[-1].split("T")[1][:5]
    return f"{start}–{end}" if start != end else f"around {start}"


def compose_message(weather, city, events):
    daily = weather["daily"]
    current = weather["current"]
    code = daily["weather_code"][0]

    summary = {
        "city": city,
        "current_temp_f": round(current["temperature_2m"]),
        "feels_like_f": round(current["apparent_temperature"]),
        "high_f": round(daily["temperature_2m_max"][0]),
        "low_f": round(daily["temperature_2m_min"][0]),
        "rain_chance_pct": daily["precipitation_probability_max"][0],
        "rain_window": find_rain_window(weather),
        "conditions": WMO_CODES.get(code, "mixed conditions"),
        "meetings": events,
    }

    prompt = (
        "Write a morning briefing text under 280 chars. Be specific and practical. "
        "Include: city, feels-like temp, high/low, rain timing if any, what to wear, "
        f"meetings with timing tips. No fluff, no quotes.\n\nData: {json.dumps(summary)}"
    )
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={os.environ['GEMINI_API_KEY']}",
        headers={"Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=30,
    )
    result = resp.json()
    print(f"Gemini response: {result}")
    if "candidates" not in result:
        raise Exception(f"Gemini error: {result}")
    return result["candidates"][0]["content"]["parts"][0]["text"].strip()


def send_sms(body):
    TwilioClient(
        os.environ["TWILIO_ACCOUNT_SID"],
        os.environ["TWILIO_AUTH_TOKEN"],
    ).messages.create(
        body=body,
        from_="whatsapp:+14155238886",
        to=f"whatsapp:{os.environ['MY_PHONE_NUMBER']}",
    )


@app.route("/weather", methods=["POST"])
def weather():
    data = request.get_json()
    if not data or "lat" not in data or "lon" not in data:
        return jsonify({"error": "Missing lat/lon"}), 400

    lat = float(data["lat"])
    lon = float(data["lon"])
    events = data.get("events", [])

    city = reverse_geocode(lat, lon)
    forecast = get_weather(lat, lon)
    message = compose_message(forecast, city, events)
    send_sms(message)

    return jsonify({"ok": True, "message": message})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
