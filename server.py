from flask import Flask, request, jsonify, render_template_string
import threading
import time
from pywebpush import webpush, WebPushException

app = Flask(__name__)

# --- VAPID keys ---
VAPID_PUBLIC_KEY = "BCfQs-j8gGE_o7l64blPSF1eCIkNYbO67bXC_PHDruv7jKbo4YamnHj0Ko1YWd6M1HfnYsM-VVSDVpQOsQI9NIk"
VAPID_PRIVATE_KEY = "SwezzCbMxAFdJKOuTwDw0DpnhnCEUhUaUvaEixUYiwo"
VAPID_CLAIMS = {"sub": "mailto:BRGYAlertSystem@ph.com"}

# --- Subscribers list ---
subscribers = []

# --- Shared state ---
arduino_triggered = False
alert_sending = False
alert_progress = 0


@app.route("/")
def home():
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Barangay Alert Dashboard</title>
        <style>
            body {{ font-family: Arial, sans-serif; background: #f0f4f8; color: #333;
                   display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
            h1 {{ color: #e74c3c; }}
            button {{ padding: 10px 20px; font-size: 16px; margin-top: 20px; cursor: pointer; }}
            #status {{ font-size: 20px; margin-top: 20px; }}
            .progress-container {{ width: 60%; background: #ddd; border-radius: 10px; margin-top: 20px; height: 30px; overflow: hidden; }}
            .progress-bar {{ height: 100%; width: 0%; background-color: #3498db; text-align: center; color: white; line-height: 30px; transition: width 0.3s; }}
            .loader {{ border: 6px solid #f3f3f3; border-top: 6px solid #3498db; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin-top: 20px; display: none; }}
            @keyframes spin {{ 100% {{ transform: rotate(360deg); }} }}
        </style>
    </head>
    <body>
        <h1>🚨 Barangay Alert Dashboard 🚨</h1>
        <button onclick="subscribeUser()">Subscribe to Alerts</button>
        <button onclick="triggerTestAlert()">Test Alert</button>
        <div id="status">Waiting for Arduino response...</div>
        <div class="progress-container">
            <div class="progress-bar" id="progress-bar">0%</div>
        </div>
        <div class="loader" id="loader"></div>

        <script>
            const vapidKey = "{VAPID_PUBLIC_KEY}";

            // Register service worker and subscribe user
            async function subscribeUser() {{
                const reg = await navigator.serviceWorker.register("/service-worker.js");
                const permission = await Notification.requestPermission();
                if (permission !== "granted") {{
                    alert("Please allow notifications to receive alerts.");
                    return;
                }}

                const sub = await reg.pushManager.subscribe({{
                    userVisibleOnly: true,
                    applicationServerKey: urlBase64ToUint8Array(vapidKey)
                }});

                await fetch("/subscribe", {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: JSON.stringify(sub)
                }});

                alert("✅ Subscribed for alerts!");
            }}

            function triggerTestAlert() {{
                fetch('/send_alert', {{method: 'GET'}})
                .then(res => res.json())
                .then(data => console.log(data));
            }}

            function updateStatus() {{
                fetch('/check_status')
                    .then(response => response.json())
                    .then(data => {{
                        const status = document.getElementById('status');
                        const bar = document.getElementById('progress-bar');
                        const loader = document.getElementById('loader');

                        if (data.arduino_triggered && !data.alert_sending) {{
                            status.innerText = "⚠️ Arduino triggered, preparing alerts...";
                            loader.style.display = "none";
                        }}
                        else if (data.alert_sending) {{
                            status.innerText = "⚠️ Sending alerts...";
                            loader.style.display = "none";
                            bar.style.width = data.progress + '%';
                            bar.innerText = data.progress + '%';
                        }}
                        else if (!data.arduino_triggered && !data.alert_sending) {{
                            status.innerText = "Waiting for Arduino response...";
                            loader.style.display = "block";
                            bar.style.width = '0%';
                            bar.innerText = '0%';
                        }}

                        setTimeout(updateStatus, 1000);
                    }})
                    .catch(err => setTimeout(updateStatus, 5000));
            }}

            function urlBase64ToUint8Array(base64String) {{
                const padding = "=".repeat((4 - base64String.length % 4) % 4);
                const base64 = (base64String + padding).replace(/\\-/g, "+").replace(/_/g, "/");
                const rawData = atob(base64);
                const outputArray = new Uint8Array(rawData.length);
                for (let i = 0; i < rawData.length; ++i) outputArray[i] = rawData.charCodeAt(i);
                return outputArray;
            }}

            updateStatus();
        </script>
    </body>
    </html>
    """
    return render_template_string(html)


@app.route("/service-worker.js")
def sw():
    js = """
    self.addEventListener("push", function(event) {
      const data = event.data ? event.data.text() : "Barangay Alert!";
      event.waitUntil(
        self.registration.showNotification("🚨 Barangay Alert 🚨", {
          body: data,
          icon: "https://upload.wikimedia.org/wikipedia/commons/e/e7/Alert_icon.svg"
        })
      );
    });
    """
    return js, 200, {'Content-Type': 'application/javascript'}


@app.route("/subscribe", methods=["POST"])
def subscribe():
    sub = request.get_json()
    subscribers.append(sub)
    print("✅ New subscriber added")
    return jsonify({"status": "Subscribed"}), 201


@app.route("/arduino_trigger", methods=["POST"])
def arduino_trigger():
    global arduino_triggered, alert_sending
    arduino_triggered = True
    alert_sending = True
    threading.Thread(target=send_alert).start()
    return jsonify({"status": "Arduino alert received"}), 200


@app.route("/send_alert", methods=["GET"])
def send_alert_manual():
    global arduino_triggered, alert_sending
    arduino_triggered = True
    alert_sending = True
    threading.Thread(target=send_alert).start()
    return jsonify({"status": "Manual alert triggered"}), 200


@app.route("/check_status")
def check_status():
    return jsonify({
        "arduino_triggered": arduino_triggered,
        "alert_sending": alert_sending,
        "progress": alert_progress
    })


def send_alert():
    global alert_progress, alert_sending, arduino_triggered
    total = len(subscribers)
    if total == 0:
        print("⚠️ No subscribers to notify.")
        alert_sending = False
        arduino_triggered = False
        return

    for idx, sub in enumerate(subscribers):
        try:
            webpush(
                subscription_info=sub,
                data="🚨 Mataas na antas ng tubig! Lumikas agad!",
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS
            )
            print(f"✅ Sent alert to subscriber {idx+1}")
        except WebPushException as e:
            print(f"❌ Failed to send alert {idx+1}: {e}")

        alert_progress = int(((idx+1)/total)*100)
        time.sleep(0.1)

    time.sleep(1)
    alert_sending = False
    arduino_triggered = False
    alert_progress = 0


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
