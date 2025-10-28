from flask import Flask, request, jsonify, render_template_string
from pywebpush import webpush, WebPushException
import threading
import time

app = Flask(__name__)

# === CONFIGURATION ===
VAPID_PUBLIC_KEY = "BCfQs-j8gGE_o7l64blPSF1eCIkNYbO67bXC_PHDruv7jKbo4YamnHj0Ko1YWd6M1HfnYsM-VVSDVpQOsQI9NIk"
VAPID_PRIVATE_KEY = "SwezzCbMxAFdJKOuTwDw0DpnhnCEUhUaEixUYiwo"
VAPID_CLAIMS = {"sub": "mailto:BRGYAlertSystem@ph.com"}

subscribers = []  # Stores all browser subscriptions
arduino_triggered = False
alert_sending = False
alert_progress = 0


# === USER PAGE ===
@app.route("/user")
def user_page():
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Barangay Alert - User</title>
        <style>
            body {{ font-family: Arial; background: #f0f4f8; text-align: center; padding-top: 100px; }}
            h1 {{ color: #e74c3c; }}
            button {{ padding: 12px 20px; font-size: 16px; cursor: pointer; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <h1>🚨 Barangay Alert - Resident Portal 🚨</h1>
        <p>Receive real-time barangay emergency alerts.</p>
        <button onclick="subscribeUser()">Subscribe for Alerts</button>

        <script>
            const vapidKey = "{VAPID_PUBLIC_KEY}";
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
                alert("✅ You are now subscribed for barangay alerts!");
            }}
            function urlBase64ToUint8Array(base64String) {{
                const padding = "=".repeat((4 - base64String.length % 4) % 4);
                const base64 = (base64String + padding).replace(/\\-/g, "+").replace(/_/g, "/");
                const rawData = atob(base64);
                const outputArray = new Uint8Array(rawData.length);
                for (let i = 0; i < rawData.length; ++i) outputArray[i] = rawData.charCodeAt(i);
                return outputArray;
            }}
        </script>
    </body>
    </html>
    """
    return render_template_string(html)


# === ADMIN PAGE ===
@app.route("/admin")
def admin_page():
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Barangay Alert Dashboard</title>
        <style>
            body { font-family: Arial; background: #f8f9fa; text-align: center; padding-top: 80px; }
            h1 { color: #c0392b; }
            button { padding: 10px 20px; font-size: 16px; margin: 10px; cursor: pointer; }
            .progress-container { width: 60%; background: #ddd; border-radius: 10px; margin: 20px auto; height: 30px; overflow: hidden; }
            .progress-bar { height: 100%; width: 0%; background-color: #3498db; color: white; text-align: center; line-height: 30px; transition: width 0.3s; }
        </style>
    </head>
    <body>
        <h1>🧑‍💼 Barangay Admin Dashboard</h1>
        <p>Monitor system status and send alerts to all residents.</p>
        <button onclick="triggerAlert()">🚨 Send Emergency Alert</button>
        <div class="progress-container">
            <div class="progress-bar" id="bar">0%</div>
        </div>
        <div id="status">Waiting for Arduino trigger...</div>

        <script>
            function triggerAlert() {
                fetch('/send_alert').then(r => r.json()).then(console.log);
            }

            function updateStatus() {
                fetch('/check_status')
                    .then(r => r.json())
                    .then(data => {
                        const bar = document.getElementById("bar");
                        const status = document.getElementById("status");
                        bar.style.width = data.progress + "%";
                        bar.innerText = data.progress + "%";
                        if (data.alert_sending) status.innerText = "⚠️ Sending alerts...";
                        else if (data.arduino_triggered) status.innerText = "✅ Trigger received.";
                        else status.innerText = "Waiting for Arduino trigger...";
                        setTimeout(updateStatus, 1000);
                    })
                    .catch(() => setTimeout(updateStatus, 3000));
            }
            updateStatus();
        </script>
    </body>
    </html>
    """
    return render_template_string(html)


# === SERVICE WORKER ===
@app.route("/service-worker.js")
def service_worker():
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
    return js, 200, {"Content-Type": "application/javascript"}


# === SUBSCRIBE ENDPOINT ===
@app.route("/subscribe", methods=["POST"])
def subscribe():
    sub = request.get_json()
    subscribers.append(sub)
    print("✅ New subscriber added.")
    return jsonify({"status": "Subscribed"}), 201


# === ARDUINO TRIGGER ===
@app.route("/arduino_trigger", methods=["POST"])
def arduino_trigger():
    global arduino_triggered, alert_sending
    arduino_triggered = True
    alert_sending = True
    threading.Thread(target=send_alert).start()
    return jsonify({"status": "Arduino alert received"}), 200


# === MANUAL ALERT (ADMIN) ===
@app.route("/send_alert", methods=["GET"])
def send_alert_manual():
    global arduino_triggered, alert_sending
    arduino_triggered = True
    alert_sending = True
    threading.Thread(target=send_alert).start()
    return jsonify({"status": "Manual alert triggered"}), 200


# === STATUS CHECK ===
@app.route("/check_status")
def check_status():
    return jsonify({
        "arduino_triggered": arduino_triggered,
        "alert_sending": alert_sending,
        "progress": alert_progress
    })


# === ALERT LOGIC ===
def send_alert():
    global alert_progress, alert_sending, arduino_triggered
    total = len(subscribers)
    if total == 0:
        print("⚠️ No subscribers found.")
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
            print(f"✅ Alert sent to subscriber {idx+1}")
        except WebPushException as e:
            print(f"❌ Failed to send alert {idx+1}: {e}")
        alert_progress = int(((idx + 1) / total) * 100)
        time.sleep(0.05)

    alert_progress = 100
    time.sleep(1)
    alert_sending = False
    arduino_triggered = False
    alert_progress = 0


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
