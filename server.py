from flask import Flask, request, jsonify, render_template_string, send_from_directory
from pywebpush import webpush, WebPushException
import threading
import time
import os

app = Flask(__name__)

# === CONFIGURATION ===
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "BNex77xpbLX96KVS1zJhT0EthcP8rJYCnu5dTL_AO0t_5ewtTPKgmqmknWaJ_2WepQgQjodcxGcFGHhq_xdyR_E")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "BkPQ0-CUpnv6QSV87QpPwdlUX9ADMRLQ5dKHXYZL-f4")
VAPID_CLAIMS = {"sub": "mailto:dukeharveylingcodo@gmail.com"}

subscribers = []
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
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Barangay Alert - Resident</title>
      <style>
        body {{ font-family: Arial; background: #eef3f7; text-align: center; padding-top: 80px; }}
        h1 {{ color: #e74c3c; }}
        button {{ background:#3498db; color:#fff; border:none; border-radius:6px; padding:12px 20px; font-size:16px; cursor:pointer; }}
        button:hover {{ background:#2980b9; }}
      </style>
    </head>
    <body>
      <h1>🚨 Barangay Alert - Resident Portal</h1>
      <p>Receive real-time barangay emergency alerts.</p>
      <button onclick="subscribeUser()">Subscribe for Alerts</button>
      <div id="alertStatus" style="margin-top: 20px; padding: 15px; border-radius: 6px; display: none;"></div>

      <script>
        let isSubscribed = false;
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
          isSubscribed = true;
          alert("✅ You are now subscribed for barangay alerts!");
          
          // Show subscription status
          const statusDiv = document.getElementById("alertStatus");
          statusDiv.style.display = "block";
          statusDiv.style.background = "#d4edda";
          statusDiv.style.color = "#155724";
          statusDiv.innerHTML = "<strong>✅ Subscribed!</strong><br>You will receive notifications when emergency alerts are sent.";
        }}
        
        // Handle subscription changes (when subscription expires)
        async function checkAndRenewSubscription() {{
          if ("serviceWorker" in navigator && "PushManager" in window) {{
            try {{
              const reg = await navigator.serviceWorker.ready;
              const subscription = await reg.pushManager.getSubscription();
              
              if (!subscription) {{
                // Subscription expired, ask user to resubscribe
                const statusDiv = document.getElementById("alertStatus");
                if (statusDiv && isSubscribed) {{
                  statusDiv.style.display = "block";
                  statusDiv.style.background = "#fff3cd";
                  statusDiv.style.color = "#856404";
                  statusDiv.innerHTML = "<strong>⚠️ Subscription Expired</strong><br>Please click the subscribe button again to continue receiving alerts.";
                  isSubscribed = false;
                }}
              }} else {{
                // Update subscription on server
                await fetch("/subscribe", {{
                  method: "POST",
                  headers: {{ "Content-Type": "application/json" }},
                  body: JSON.stringify(subscription)
                }});
              }}
            }} catch (err) {{
              console.log("Subscription check error:", err);
            }}
          }}
        }}
        
        // Check subscription status when page loads
        checkAndRenewSubscription();
        
        // Check if notifications are supported
        if ("Notification" in window && "serviceWorker" in navigator) {{
          console.log("Push notifications supported!");
        }} else {{
          alert("Your browser does not support push notifications.");
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
      <title>Barangay Admin Dashboard</title>
      <style>
        body { font-family: Arial; background: #f8f9fa; text-align: center; padding-top: 60px; }
        h1 { color: #c0392b; }
        button { background:#e74c3c; color:#fff; border:none; border-radius:6px; padding:12px 20px; font-size:16px; cursor:pointer; }
        button:hover { background:#c0392b; }
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
      <div id="status" style="margin-top: 10px; font-weight: bold;">Ready. Click button to send emergency alert.</div>
      <div id="subscriberCount" style="margin-top: 10px; color: #7f8c8d;">No subscribers yet</div>

      <script>
        function triggerAlert() {
          fetch('/send_alert')
            .then(r => r.json())
            .then(data => {
              console.log("Alert triggered:", data);
              const status = document.getElementById("status");
              status.innerText = "⚠️ Sending emergency alert to all subscribers...";
            })
            .catch(err => {
              console.error("Error:", err);
              alert("Failed to send alert. Please try again.");
            });
        }

        function updateStatus() {
          fetch('/check_status')
            .then(r => r.json())
            .then(data => {
              const bar = document.getElementById("bar");
              const status = document.getElementById("status");
              bar.style.width = data.progress + "%";
              bar.innerText = data.progress + "%";
              
              if (data.alert_sending) {
                status.innerText = `⚠️ Sending alerts... ${data.progress}%`;
                status.style.color = "#e67e22";
              } else if (data.arduino_triggered) {
                status.innerText = "✅ Alert sent successfully to all subscribers!";
                status.style.color = "#27ae60";
              } else {
                status.innerText = "Ready. Click button to send emergency alert.";
                status.style.color = "#2c3e50";
              }
              
              // Update subscriber count
              fetch('/get_subscribers')
                .then(r => r.json())
                .then(result => {
                  const countDiv = document.getElementById("subscriberCount");
                  const count = result.count || 0;
                  countDiv.innerText = `📱 Total Subscribers: ${count}`;
                  if (count === 0) {
                    countDiv.style.color = "#e74c3c";
                    countDiv.innerHTML += " ⚠️ No subscribers yet. Users need to subscribe first.";
                  } else {
                    countDiv.style.color = "#27ae60";
                  }
                })
                .catch(() => {});
              
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
    js = f"""
    const VAPID_PUBLIC_KEY = "{VAPID_PUBLIC_KEY}";
    
    self.addEventListener("push", function(event) {{
      const data = event.data ? event.data.text() : "Barangay Alert!";
      event.waitUntil(
        self.registration.showNotification("🚨 Barangay Alert 🚨", {{
          body: data,
          icon: "https://upload.wikimedia.org/wikipedia/commons/e/e7/Alert_icon.svg",
          badge: "https://upload.wikimedia.org/wikipedia/commons/e/e7/Alert_icon.svg",
          tag: "barangay-alert",
          requireInteraction: true
        }})
      );
    }});
    
    // Handle subscription changes (refresh expired subscriptions)
    self.addEventListener("pushsubscriptionchange", function(event) {{
      console.log("Push subscription changed");
      event.waitUntil(
        self.registration.pushManager.subscribe({{
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY)
        }}).then(function(subscription) {{
          return fetch("/subscribe", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify(subscription)
          }});
        }})
      );
    }});
    
    function urlBase64ToUint8Array(base64String) {{
      const padding = "=".repeat((4 - base64String.length % 4) % 4);
      const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
      const rawData = atob(base64);
      const outputArray = new Uint8Array(rawData.length);
      for (let i = 0; i < rawData.length; ++i) {{
        outputArray[i] = rawData.charCodeAt(i);
      }}
      return outputArray;
    }}
    """
    return js, 200, {"Content-Type": "application/javascript"}


# === SUBSCRIBE ENDPOINT ===
@app.route("/subscribe", methods=["POST"])
def subscribe():
    global subscribers
    sub = request.get_json()
    
    # Check if subscription already exists (by endpoint URL)
    if sub and 'endpoint' in sub:
        # Remove old subscription if exists (to prevent duplicates)
        subscribers = [s for s in subscribers if s.get('endpoint') != sub.get('endpoint')]
    
    subscribers.append(sub)
    print(f"✅ New subscriber added. Total subscribers: {len(subscribers)}")
    return jsonify({"status": "Subscribed", "total_subscribers": len(subscribers)}), 201


# === ARDUINO TRIGGER ===
@app.route("/arduino_trigger", methods=["POST"])
def arduino_trigger():
    global arduino_triggered, alert_sending
    arduino_triggered = True
    alert_sending = True
    threading.Thread(target=send_alert).start()
    return jsonify({"status": "Arduino alert received"}), 200


# === MANUAL ALERT (ADMIN) ===
@app.route("/send_alert", methods=["GET", "POST"])
def send_alert_manual():
    global arduino_triggered, alert_sending, alert_progress
    if alert_sending:
        return jsonify({"status": "Alert already sending", "message": "Please wait for current alert to finish"}), 400
    
    arduino_triggered = True
    alert_sending = True
    alert_progress = 0
    threading.Thread(target=send_alert).start()
    return jsonify({
        "status": "success", 
        "message": "Emergency alert triggered and sending to all subscribers",
        "subscribers": len(subscribers)
    }), 200


# === STATUS CHECK ===
@app.route("/check_status")
def check_status():
    return jsonify({
        "arduino_triggered": arduino_triggered,
        "alert_sending": alert_sending,
        "progress": alert_progress
    })

# === GET SUBSCRIBERS COUNT ===
@app.route("/get_subscribers")
def get_subscribers():
    return jsonify({
        "count": len(subscribers)
    })


# === ALERT LOGIC ===
def send_alert():
    global alert_progress, alert_sending, arduino_triggered, subscribers
    total = len(subscribers)
    if total == 0:
        print("⚠️ No subscribers found.")
        alert_sending = False
        arduino_triggered = False
        alert_progress = 0
        return

    print(f"🚨 Starting to send emergency alert to {total} subscriber(s)...")
    success_count = 0
    failed_count = 0
    expired_subscriptions = []
    
    # Make a copy to iterate safely since we'll modify the list
    subscribers_copy = list(subscribers)
    
    for idx, sub in enumerate(subscribers_copy):
        try:
            webpush(
                subscription_info=sub,
                data="🚨 URGENT: Emergency Alert from Barangay! Please check immediately!",
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=VAPID_CLAIMS
            )
            print(f"✅ Alert sent to subscriber {idx+1}/{total}")
            success_count += 1
        except WebPushException as e:
            # Check if subscription is expired or invalid (410 Gone, 404 Not Found, 403 Forbidden)
            error_message = str(e).lower()
            is_permanent_error = False
            
            # Check error message for common expiration indicators
            if "410" in str(e) or "gone" in error_message or "expired" in error_message or "unsubscribed" in error_message:
                is_permanent_error = True
                expired_subscriptions.append(sub)
                print(f"⚠️ Subscriber {idx+1} subscription expired/invalid (will be removed)")
            elif "404" in str(e) or "not found" in error_message:
                is_permanent_error = True
                expired_subscriptions.append(sub)
                print(f"⚠️ Subscriber {idx+1} subscription not found (will be removed)")
            elif "403" in str(e) or "forbidden" in error_message:
                is_permanent_error = True
                expired_subscriptions.append(sub)
                print(f"⚠️ Subscriber {idx+1} subscription forbidden (will be removed)")
            
            if not is_permanent_error:
                print(f"❌ Failed to send alert to subscriber {idx+1}: {e}")
            
            failed_count += 1
        except Exception as e:
            print(f"❌ Unexpected error sending to subscriber {idx+1}: {e}")
            failed_count += 1
        
        alert_progress = int(((idx + 1) / total) * 100)
        time.sleep(0.1)

    # Remove expired subscriptions
    if expired_subscriptions:
        for expired_sub in expired_subscriptions:
            if expired_sub in subscribers:
                subscribers.remove(expired_sub)
        print(f"🧹 Removed {len(expired_subscriptions)} expired subscription(s)")

    alert_progress = 100
    print(f"✅ Alert sending complete! Success: {success_count}, Failed: {failed_count}")
    alert_sending = False
    # Keep arduino_triggered True for 3 seconds to show success message
    time.sleep(3)
    arduino_triggered = False
    alert_progress = 0


@app.route("/")
def home():
    return "<h3>Barangay Alert System is running. Visit /user or /admin.</h3>"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
