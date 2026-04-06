import os
import json
import requests
from flask import Flask, request, jsonify
from twilio.rest import Client as TwilioClient

app = Flask(__name__)


def get_weather(lat, lon):
    params = {"lat": lat, "lon": lon, "appid": os.environ["OWM_API_KEY"], "units": "imperial"}
    current = requests.get("https://api.openweathermap.org/data/2.5/weather", params=params, timeout=5).json()
    forecast = requests.get("https://api.openweathermap.org/data/2.5/forecast", params=params, timeout=5).json()
    print(f"OWM current: {current}")
    print(f"OWM forecast: {forecast}")
    if current.get("cod") != 200:
        raise Exception(f"OWM error: {current.get('message', 'unknown')}")
    if str(forecast.get("cod")) != "200":
        raise Exception(f"OWM forecast error: {forecast.get('message', 'unknown')}")
    return {"current": current, "forecast": forecast}


def find_rain_window(weather):
    today = weather["forecast"]["list"][0]["dt_txt"][:10]
    entries = [e for e in weather["forecast"]["list"] if e["dt_txt"].startswith(today)]
    rain_entries = [
        e for e in entries
        if e.get("pop", 0) >= 0.4 and 6 <= int(e["dt_txt"][11:13]) <= 22
    ]
    if not rain_entries:
        return None
    start = rain_entries[0]["dt_txt"][11:16]
    end = rain_entries[-1]["dt_txt"][11:16]
    return f"{start}–{end}" if start != end else f"around {start}"


def compose_message(weather, events):
    current = weather["current"]
    forecast_list = weather["forecast"]["list"]
    city = current.get("name") or "your area"

    today = forecast_list[0]["dt_txt"][:10]
    today_entries = [e for e in forecast_list if e["dt_txt"].startswith(today)]

    summary = {
        "city": city,
        "current_temp_f": round(current["main"]["temp"]),
        "feels_like_f": round(current["main"]["feels_like"]),
        "high_f": round(max(e["main"]["temp_max"] for e in today_entries)),
        "low_f": round(min(e["main"]["temp_min"] for e in today_entries)),
        "rain_chance_pct": round(max(e.get("pop", 0) for e in today_entries) * 100),
        "rain_window": find_rain_window(weather),
        "conditions": current["weather"][0]["description"],
        "meetings": events,
    }

    prompt = (
        "Write a cheerful, upbeat morning briefing text under 280 chars. Be warm and fun — "
        "use a light pun or playful tone to put the reader in a good mood. Still include the "
        "key info: city, feels-like temp, high/low, rain timing if any, what to wear, and "
        "meetings with timing tips. No quotes, no fluff, just good vibes and useful info.\n\n"
        f"Data: {json.dumps(summary)}"
    )
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={os.environ['GEMINI_API_KEY']}",
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"thinkingConfig": {"thinkingBudget": 0}},
        },
        timeout=60,
    ).json()
    print(f"Gemini response: {resp}")
    if "candidates" not in resp:
        raise Exception(f"Gemini error: {resp}")
    return resp["candidates"][0]["content"]["parts"][0]["text"].strip()


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

    weather_data = get_weather(lat, lon)
    message = compose_message(weather_data, events)
    send_sms(message)

    return jsonify({"ok": True, "message": message})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
