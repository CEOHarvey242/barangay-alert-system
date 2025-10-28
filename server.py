from flask import Flask, request, jsonify, render_template_string, send_from_directory, Response
from pywebpush import webpush, WebPushException
import threading
import time
import os

app = Flask(__name__)

# Add CSP headers to allow inline scripts (needed for Render)
@app.after_request
def set_csp_header(response):
    # Allow inline scripts for our own code - needed for inline JavaScript in templates
    # Remove existing CSP if any to avoid conflicts
    if 'Content-Security-Policy' in response.headers:
        del response.headers['Content-Security-Policy']
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https:; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self' https:;"
    return response

# === CONFIGURATION ===
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "BNex77xpbLX96KVS1zJhT0EthcP8rJYCnu5dTL_AO0t_5ewtTPKgmqmknWaJ_2WepQgQjodcxGcFGHhq_xdyR_E")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "BkPQ0-CUpnv6QSV87QpPwdlUX9ADMRLQ5dKHXYZL-f4")
VAPID_CLAIMS = {"sub": "mailto:dukeharveylingcodo@gmail.com"}

subscribers = []  # Push notification subscribers
simple_notification_users = set()  # Users registered for simple notifications (by session/IP)
active_pollers = {}  # Track active users checking for alerts (last_check_time)
arduino_triggered = False
alert_sending = False
alert_progress = 0
alert_history = []  # Store recent alerts for polling
last_alert_id = 0


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
         @keyframes pulse {{
           0% {{ transform: scale(1); }}
           50% {{ transform: scale(1.02); }}
           100% {{ transform: scale(1); }}
         }}
         #alertStatus {{
           transition: all 0.3s ease;
         }}
       </style>
    </head>
    <body>
      <h1>🚨 Barangay Alert - Resident Portal</h1>
      <p>Receive real-time barangay emergency alerts.</p>
      <button onclick="subscribeUser()">Subscribe for Alerts</button>
      <div id="alertStatus" style="margin-top: 20px; padding: 15px; border-radius: 6px; display: none;"></div>

      <script>
        let notificationEnabled = false;
        let lastAlertId = 0;
        let checkInterval = null;
        
        async function subscribeUser() {{
          const statusDiv = document.getElementById("alertStatus");
          
          // Check if Notification API is supported
          if (!("Notification" in window)) {{
            statusDiv.style.display = "block";
            statusDiv.style.background = "#f8d7da";
            statusDiv.style.color = "#721c24";
            statusDiv.innerHTML = "<strong>⚠️ Browser Not Supported</strong><br>" +
              "Your browser doesn't support notifications. Please use Chrome, Firefox, Edge, or Safari.";
            return;
          }}
          
          try {{
            statusDiv.style.display = "block";
            statusDiv.style.background = "#d1ecf1";
            statusDiv.style.color = "#0c5460";
            statusDiv.innerHTML = "⏳ Requesting notification permission...";
            
            // Request notification permission
            const permission = await Notification.requestPermission();
            
            if (permission === "granted") {{
              notificationEnabled = true;
              statusDiv.style.background = "#d4edda";
              statusDiv.style.color = "#155724";
              statusDiv.innerHTML = "<strong>✅ Notifications Enabled!</strong><br>" +
                "You will receive alerts when emergency notifications are sent.";
              
              // Register for polling
              try {{
                const regResponse = await fetch("/register_user", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
                  body: JSON.stringify({{ enabled: true }})
                }});
                const regData = await regResponse.json();
                console.log("Registration response:", regData);
              }} catch (regErr) {{
                console.error("Registration error:", regErr);
                // Continue anyway - polling will still work
              }}
              
              // Start checking for alerts
              startCheckingAlerts();
              
              console.log("✅ Notifications enabled");
            }} else if (permission === "denied") {{
              statusDiv.style.background = "#fff3cd";
              statusDiv.style.color = "#856404";
              statusDiv.innerHTML = "<strong>⚠️ Permission Denied</strong><br>" +
                "Please allow notifications in your browser settings to receive alerts.";
            }} else {{
              statusDiv.style.background = "#fff3cd";
              statusDiv.style.color = "#856404";
              statusDiv.innerHTML = "<strong>⚠️ Permission Not Set</strong><br>" +
                "Please click the button again and allow notifications when prompted.";
            }}
          }} catch (err) {{
            console.error("Error enabling notifications:", err);
            statusDiv.style.background = "#f8d7da";
            statusDiv.style.color = "#721c24";
            statusDiv.innerHTML = "<strong>❌ Error</strong><br>" +
              "Failed to enable notifications. Please try again.";
          }}
        }}
        
         function showNotification(title, message) {{
           if (Notification.permission === "granted") {{
             try {{
               const notification = new Notification(title, {{
                 body: message,
                 icon: "https://upload.wikimedia.org/wikipedia/commons/e/e7/Alert_icon.svg",
                 badge: "https://upload.wikimedia.org/wikipedia/commons/e/e7/Alert_icon.svg",
                 tag: "barangay-alert",
                 requireInteraction: true,
                 vibrate: [200, 100, 200],  // Vibrate on mobile
                 sound: "",  // Some mobile browsers use sound
                 silent: false
               }});
               
               notification.onclick = function() {{
                 window.focus();
                 notification.close();
               }};
               
               // Auto close after 15 seconds (longer for mobile)
               setTimeout(() => notification.close(), 15000);
               
               // Also show alert for mobile browsers that don't support notifications well
               console.log("Notification shown:", title, message);
             }} catch (err) {{
               console.error("Error showing notification:", err);
               // Fallback: show alert for mobile
               alert(title + "\\n\\n" + message);
             }}
           }} else {{
             // Fallback for when permission not granted
             alert(title + "\\n\\n" + message);
           }}
         }}
        
         function startCheckingAlerts() {{
           // Check every 3 seconds for new alerts
           if (checkInterval) clearInterval(checkInterval);
           
           // Immediate first check to register user
           (async () => {{
             try {{
               const response = await fetch("/check_alerts?last_id=" + lastAlertId);
               const data = await response.json();
               if (data.alert) lastAlertId = data.alert.id || 0;
             }} catch (err) {{
               console.error("Initial check error:", err);
             }}
           }})();
           
           checkInterval = setInterval(async () => {{
             // Check even without notification permission - use visual alert instead
             try {{
               const response = await fetch("/check_alerts?last_id=" + lastAlertId);
               const data = await response.json();
               
               if (data.has_new_alert && data.alert) {{
                 lastAlertId = data.alert.id || lastAlertId;
                 
                 // Show notification
                 showNotification("🚨 Barangay Emergency Alert 🚨", data.alert.message);
                 
                 // Also vibrate on mobile if supported
                 if (navigator.vibrate) {{
                   navigator.vibrate([200, 100, 200, 100, 200]);
                 }}
                 
                 // Update status with visual alert
                 const statusDiv = document.getElementById("alertStatus");
                 if (statusDiv) {{
                   statusDiv.style.background = "#f8d7da";
                   statusDiv.style.color = "#721c24";
                   statusDiv.style.border = "2px solid #e74c3c";
                   statusDiv.style.animation = "pulse 2s infinite";
                   statusDiv.innerHTML = "<strong>🚨🔔 NEW ALERT RECEIVED! 🔔🚨</strong><br><br>" + 
                     data.alert.message + 
                     "<br><br><small>Tap to dismiss</small>";
                   
                   // Make it clickable to dismiss
                   statusDiv.style.cursor = "pointer";
                   statusDiv.onclick = function() {{
                     statusDiv.style.background = "#d4edda";
                     statusDiv.style.color = "#155724";
                     statusDiv.style.border = "none";
                     statusDiv.style.animation = "none";
                     statusDiv.innerHTML = "<strong>✅ Notifications Enabled!</strong><br>" +
                       "You will receive alerts when emergency notifications are sent.";
                     statusDiv.style.cursor = "default";
                     statusDiv.onclick = null;
                   }};
                   
                   // Auto reset after 30 seconds (longer for mobile)
                   setTimeout(() => {{
                     if (statusDiv.style.background === "#f8d7da") {{
                       statusDiv.style.background = "#d4edda";
                       statusDiv.style.color = "#155724";
                       statusDiv.style.border = "none";
                       statusDiv.style.animation = "none";
                       statusDiv.innerHTML = "<strong>✅ Notifications Enabled!</strong><br>" +
                         "You will receive alerts when emergency notifications are sent.";
                       statusDiv.style.cursor = "default";
                       if (statusDiv.onclick) statusDiv.onclick = null;
                     }}
                   }}, 30000);
                 }}
               }}
             }} catch (err) {{
               console.error("Error checking alerts:", err);
             }}
           }}, 3000);
         }}
        
        // Check permission status on load
        window.addEventListener("load", function() {{
          const statusDiv = document.getElementById("alertStatus");
          
          if ("Notification" in window) {{
            if (Notification.permission === "granted") {{
              notificationEnabled = true;
              statusDiv.style.display = "block";
              statusDiv.style.background = "#d4edda";
              statusDiv.style.color = "#155724";
              statusDiv.innerHTML = "<strong>✅ Notifications Enabled!</strong><br>" +
                "You will receive alerts when emergency notifications are sent.";
              startCheckingAlerts();
              
              // Get last alert ID
              fetch("/check_alerts").then(r => r.json()).then(data => {{
                if (data.alert) lastAlertId = data.alert.id;
              }});
              
              // Register user
              fetch("/register_user", {{
                method: "POST",
                headers: {{ "Content-Type": "application/json" }},
                body: JSON.stringify({{ enabled: true }})
              }}).catch(err => console.log("Registration check failed:", err));
            }} else {{
              console.log("✅ Browser supports notifications - click button to enable");
            }}
          }} else {{
            statusDiv.style.display = "block";
            statusDiv.style.background = "#fff3cd";
            statusDiv.style.color = "#856404";
            statusDiv.innerHTML = "<strong>⚠️ Browser Not Supported</strong><br>" +
              "Your browser doesn't support notifications. Please use Chrome, Firefox, Edge, or Safari.";
          }}
        }});
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
      <button onclick="triggerAlert(event)">🚨 Send Emergency Alert</button>

      <div class="progress-container">
        <div class="progress-bar" id="bar">0%</div>
      </div>
      <div id="status" style="margin-top: 10px; font-weight: bold;">Ready. Click button to send emergency alert.</div>
      <div id="subscriberCount" style="margin-top: 10px; color: #7f8c8d;">No subscribers yet</div>

      <script>
        function triggerAlert(event) {
          const status = document.getElementById("status");
          const button = event ? event.target : document.querySelector('button');
          
          // Disable button temporarily
          button.disabled = true;
          button.style.opacity = "0.6";
          
          status.innerText = "⏳ Triggering alert...";
          
          fetch('/send_alert')
            .then(r => {
              if (!r.ok) {
                return r.json().then(data => {
                  throw new Error(data.message || "Failed to send alert");
                });
              }
              return r.json();
            })
            .then(data => {
              console.log("Alert triggered:", data);
              status.innerText = `⚠️ Alert sent! Notifying ${data.subscribers || 0} subscriber(s)...`;
              status.style.color = "#e67e22";
              
              // Re-enable button after a delay
              setTimeout(() => {
                button.disabled = false;
                button.style.opacity = "1";
              }, 2000);
            })
            .catch(err => {
              console.error("Error:", err);
              status.innerText = `❌ Error: ${err.message}`;
              status.style.color = "#e74c3c";
              alert("Failed to send alert: " + err.message);
              
              // Re-enable button
              button.disabled = false;
              button.style.opacity = "1";
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
                   const active = result.active_pollers || 0;
                   const registered = result.registered_users || 0;
                   
                   if (count > 0) {
                     countDiv.style.color = "#27ae60";
                     countDiv.innerHTML = `📱 Total Subscribers: <strong>${count}</strong>`;
                     if (active > 0 || registered > 0) {
                       countDiv.innerHTML += `<br><small style="color: #7f8c8d;">Active: ${active} | Registered: ${registered}</small>`;
                     }
                   } else {
                     countDiv.style.color = "#e74c3c";
                     countDiv.innerHTML = `📱 Total Subscribers: <strong>0</strong><br><small>⚠️ No subscribers yet. Users need to subscribe first.</small>`;
                   }
                   console.log("Subscriber count updated:", result);
                 })
                 .catch(err => {
                   console.error("Error fetching subscriber count:", err);
                 });
              
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
    global arduino_triggered, alert_sending, alert_progress, subscribers, simple_notification_users, active_pollers
    
    try:
        if alert_sending:
            return jsonify({
                "status": "error",
                "message": "Alert already sending. Please wait for current alert to finish."
            }), 400
        
        # Count all subscribers (simple + push)
        registered_count = len(simple_notification_users)
        active_count = len(active_pollers)
        simple_count = max(registered_count, active_count)
        push_count = len(subscribers)
        total_subscribers = simple_count + push_count
        
        if total_subscribers == 0:
            return jsonify({
                "status": "error",
                "message": "No subscribers found. Users need to subscribe first.",
                "subscribers": 0
            }), 400
        
        # Reset and start alert
        arduino_triggered = False
        alert_sending = True
        alert_progress = 0
        
        # Start sending in background thread
        thread = threading.Thread(target=send_alert)
        thread.daemon = True
        thread.start()
        
        print(f"🚨 Admin triggered alert to {total_subscribers} subscriber(s)")
        
        return jsonify({
            "status": "success", 
            "message": "Emergency alert triggered and sending to all subscribers",
            "subscribers": total_subscribers
        }), 200
        
    except Exception as e:
        print(f"❌ Error in send_alert_manual: {e}")
        alert_sending = False
        return jsonify({
            "status": "error",
            "message": f"Error triggering alert: {str(e)}"
        }), 500


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
    global simple_notification_users, active_pollers, subscribers
    
    # Count active pollers (users actively checking) + registered users + push subscribers
    active_count = len(active_pollers)
    registered_count = len(simple_notification_users)
    push_count = len(subscribers)
    
    # Combine registered users and active pollers (union of both sets)
    # This ensures we count all users who have either registered or are actively polling
    simple_count = len(simple_notification_users.union(set(active_pollers.keys())))
    total_count = simple_count + push_count
    
    # Debug logging
    print(f"📊 Subscriber count check:")
    print(f"   Active pollers: {active_count}")
    print(f"   Registered users: {registered_count}")
    print(f"   Simple users (union): {simple_count}")
    print(f"   Push subscribers: {push_count}")
    print(f"   Total count: {total_count}")
    
    return jsonify({
        "count": total_count,
        "simple_users": simple_count,
        "push_users": push_count,
        "active_pollers": active_count,
        "registered_users": registered_count
    })


# === ALERT LOGIC ===
def send_alert():
    global alert_progress, alert_sending, arduino_triggered, subscribers, simple_notification_users, last_alert_id, alert_history, active_pollers
    
    # Count both registered users and active pollers (union)
    registered_count = len(simple_notification_users)
    active_count = len(active_pollers)
    # Combine both sets to get actual unique users
    simple_count = len(simple_notification_users.union(set(active_pollers.keys())))
    push_count = len(subscribers)
    total = simple_count + push_count
    
    print(f"🔍 Alert sending check:")
    print(f"   Registered simple users: {registered_count}")
    print(f"   Active pollers: {active_count}")
    print(f"   Simple users (union): {simple_count}")
    print(f"   Push subscribers: {push_count}")
    print(f"   Total count: {total}")
    
    if total == 0:
        print("⚠️ No subscribers found.")
        alert_sending = False
        arduino_triggered = False
        alert_progress = 0
        return

    print(f"🚨 Starting to send emergency alert to {total} subscriber(s)...")
    
    # For simple notification users, just create alert entry (they poll for it)
    if simple_count > 0:
        last_alert_id += 1
        new_alert = {
            "id": last_alert_id,
            "message": "🚨 URGENT: Emergency Alert from Barangay! Please check immediately!",
            "timestamp": time.time()
        }
        alert_history.append(new_alert)
        # Keep only last 100 alerts
        if len(alert_history) > 100:
            alert_history.pop(0)
        print(f"✅ Alert created for {simple_count} simple notification user(s)")
    
    # For push notification subscribers, send via webpush
    push_count = len(subscribers)
    success_count = simple_count
    failed_count = 0
    expired_subscriptions = []
    
    if push_count > 0:
        # Make a copy to iterate safely since we'll modify the list
        subscribers_copy = list(subscribers)
        
        for idx, sub in enumerate(subscribers_copy):
            try:
                # Validate subscription before sending
                if not sub or not sub.get('endpoint'):
                    print(f"⚠️ Subscriber {idx+1} has invalid subscription data")
                    expired_subscriptions.append(sub)
                    failed_count += 1
                    continue
                
                webpush(
                    subscription_info=sub,
                    data="🚨 URGENT: Emergency Alert from Barangay! Please check immediately!",
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims=VAPID_CLAIMS,
                    ttl=86400  # 24 hours TTL for push notifications
                )
                print(f"✅ Alert sent to subscriber {idx+1}/{push_count}")
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
            
            # Calculate progress based on push subscribers only
            push_progress = int(((idx + 1) / push_count) * 100) if push_count > 0 else 100
            alert_progress = push_progress
            time.sleep(0.1)

        # Remove expired subscriptions
        if expired_subscriptions:
            for expired_sub in expired_subscriptions:
                if expired_sub in subscribers:
                    subscribers.remove(expired_sub)
            print(f"🧹 Removed {len(expired_subscriptions)} expired subscription(s)")
    else:
        # No push subscribers, just finish
        alert_progress = 100
    
    print(f"✅ Alert sending complete! Success: {success_count}, Failed: {failed_count}")
    alert_sending = False
    # Keep arduino_triggered True for 3 seconds to show success message
    time.sleep(3)
    arduino_triggered = False
    alert_progress = 0


# === SIMPLE NOTIFICATION ENDPOINTS ===
@app.route("/register_user", methods=["POST"])
def register_user():
    global simple_notification_users, active_pollers
    # Track user by IP address and user agent
    user_id = f"{request.remote_addr}_{request.headers.get('User-Agent', '')}"
    simple_notification_users.add(user_id)
    # Also add to active pollers to ensure they're counted immediately
    active_pollers[user_id] = time.time()
    
    total_users = len(simple_notification_users) + len(subscribers)
    active_count = len(active_pollers)
    
    print(f"✅ User registered for simple notifications.")
    print(f"   User ID: {user_id}")
    print(f"   Simple users: {len(simple_notification_users)}")
    print(f"   Active pollers: {active_count}")
    print(f"   Push users: {len(subscribers)}")
    print(f"   Total: {total_users}")
    
    return jsonify({
        "status": "registered",
        "total_users": total_users,
        "simple_users": len(simple_notification_users),
        "push_users": len(subscribers),
        "active_pollers": active_count
    }), 200


@app.route("/check_alerts")
def check_alerts():
    global active_pollers, simple_notification_users
    last_id = request.args.get("last_id", "0")
    try:
        last_id = int(last_id)
    except:
        last_id = 0
    
    # Track active pollers (users actively checking)
    user_identifier = f"{request.remote_addr}_{request.headers.get('User-Agent', '')}"
    
    # Always update active pollers timestamp
    active_pollers[user_identifier] = time.time()
    
    # Also add to simple_notification_users immediately
    if user_identifier not in simple_notification_users:
        simple_notification_users.add(user_identifier)
        print(f"✅ Active poller auto-registered: {len(simple_notification_users)} total simple users")
    
    # Remove inactive pollers (haven't checked in last 15 seconds - more lenient for Render)
    current_time = time.time()
    active_pollers = {k: v for k, v in active_pollers.items() if current_time - v < 15}
    
    # Check if there's a new alert
    if alert_history:
        latest_alert = alert_history[-1]
        if latest_alert["id"] > last_id:
            return jsonify({
                "has_new_alert": True,
                "alert": latest_alert
            }), 200
    
    return jsonify({
        "has_new_alert": False,
        "alert": alert_history[-1] if alert_history else None
    }), 200


@app.route("/")
def home():
    return "<h3>Barangay Alert System is running. Visit /user or /admin.</h3>"


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
