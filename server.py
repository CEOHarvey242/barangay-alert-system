from flask import Flask, request, jsonify, render_template_string
from twilio.rest import Client
import threading
import time

app = Flask(__name__)

# --- Twilio credentials ---
ACCOUNT_SID = "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
AUTH_TOKEN = "your_auth_token"
TWILIO_NUMBER = "+15075551234"
client = Client(ACCOUNT_SID, AUTH_TOKEN)

# --- Recipients (all barangay phone numbers) ---
recipients = ["+639171234567", "+639189876543"]

# --- Shared state ---
arduino_triggered = False
alert_sending = False
alert_progress = 0  # 0 to 100

# --- Homepage / Dashboard (unchanged) ---
@app.route("/")
def home():
    html = """  <!-- Your existing HTML dashboard -->
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Barangay Alert Dashboard</title>
        <style>
            body { font-family: Arial, sans-serif; background: #f0f4f8; color: #333;
                   display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; }
            h1 { color: #e74c3c; }
            #status { font-size: 20px; margin-top: 20px; }
            .progress-container { width: 60%; background: #ddd; border-radius: 10px; margin-top: 20px; height: 30px; overflow: hidden; }
            .progress-bar { height: 100%; width: 0%; background-color: #3498db; text-align: center; color: white; line-height: 30px; transition: width 0.3s; }
            .loader { border: 6px solid #f3f3f3; border-top: 6px solid #3498db; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin-top: 20px; display: none; }
            @keyframes spin { 100% { transform: rotate(360deg); } }
        </style>
    </head>
    <body>
        <h1>🚨 Barangay Alert Dashboard 🚨</h1>
        <div id="status">Waiting for Arduino response...</div>
        <div class="progress-container">
            <div class="progress-bar" id="progress-bar">0%</div>
        </div>
        <div class="loader" id="loader"></div>

        <script>
            function updateStatus() {
                fetch('/check_status')
                    .then(response => response.json())
                    .then(data => {
                        const status = document.getElementById('status');
                        const bar = document.getElementById('progress-bar');
                        const loader = document.getElementById('loader');

                        if (data.arduino_triggered && !data.alert_sending) {
                            status.innerText = "⚠️ Arduino triggered, preparing alerts...";
                            loader.style.display = "none";
                        }
                        else if (data.alert_sending) {
                            status.innerText = "⚠️ Sending alerts...";
                            loader.style.display = "none";
                            bar.style.width = data.progress + '%';
                            bar.innerText = data.progress + '%';
                        }
                        else if (!data.arduino_triggered && !data.alert_sending) {
                            status.innerText = "Waiting for Arduino response...";
                            loader.style.display = "block";
                            bar.style.width = '0%';
                            bar.innerText = '0%';
                        }

                        setTimeout(updateStatus, 500);
                    })
                    .catch(err => setTimeout(updateStatus, 5000));
            }

            updateStatus();
        </script>
    </body>
    </html>
    """
    return render_template_string(html)

# --- Arduino trigger (POST from ESP32 / remote) ---
@app.route("/arduino_trigger", methods=["POST"])
def arduino_trigger():
    global arduino_triggered, alert_sending
    arduino_triggered = True
    alert_sending = True
    # Start sending SMS in background thread
    threading.Thread(target=send_alert).start()
    return jsonify({"status": "Arduino alert received"}), 200

# --- Optional manual trigger via browser ---
@app.route("/send_alert", methods=["GET"])
def send_alert_manual():
    global arduino_triggered, alert_sending
    arduino_triggered = True
    alert_sending = True
    threading.Thread(target=send_alert).start()
    return jsonify({"status": "Manual alert triggered"}), 200

# --- Dashboard / Arduino status check ---
@app.route("/check_status")
def check_status():
    return jsonify({
        "arduino_triggered": arduino_triggered,
        "alert_sending": alert_sending,
        "progress": alert_progress
    })

# --- Send alert logic (Twilio SMS) ---
def send_alert():
    global alert_progress, alert_sending, arduino_triggered
    total = len(recipients)
    for idx, number in enumerate(recipients):
        try:
            client.messages.create(
                from_=TWILIO_NUMBER,
                to=number,
                body="🚨 Barangay Alert: Mataas na antas ng tubig! Lumikas agad!"
            )
            print(f"✅ Sent alert to {number}")
        except Exception as e:
            print(f"❌ Failed to send to {number}: {e}")

        # Update progress smoothly
        target_progress = int(((idx + 1)/total)*100)
        while alert_progress < target_progress:
            alert_progress += 1
            time.sleep(0.05)

    alert_progress = 100
    time.sleep(0.5)
    alert_sending = False
    arduino_triggered = False
    alert_progress = 0

# --- Run server ---
if __name__ == "__main__":
    # host=0.0.0.0 allows ESP32 from external network (Ngrok/VPS)
    app.run(host="0.0.0.0", port=5000)
