import logging
import json, os
import schedule, time
import threading
import notifier
import db
from settings import WEB_SETTINGS, SCHEDULE_EVERY_MINUTES
from datetime import datetime

from flask import request, Response, render_template, jsonify, Flask

app = Flask(__name__)
app.config['SECRET_KEY'] = '9OLWxND4o83j4K4iuopO'

@app.route('/')
def index():
    return render_template('index.html')

@app.route("/subscription/", methods=["GET", "POST"])
def subscription():
    """
        POST creates a subscription
        GET returns vapid public key which clients uses to send around push notification
    """
    print(request)

    if request.method == "GET":
        return Response(response=json.dumps({"public_key": notifier.VAPID_PUBLIC_KEY}),
            headers={"Access-Control-Allow-Origin": "*"}, content_type="application/json")
    
    if request.method == "POST" and request.json:
        subscription_token = request.json.get("subscription_token")
        subscription_token["expiration_time"] = subscription_token["expirationTime"]
        subscription_token["keys_p256dh"] = subscription_token["keys"]["p256dh"]
        subscription_token["keys_auth"] = subscription_token["keys"]["auth"]
        db.add_subscriber(subscription_token)
        return Response(response=json.dumps({'success':1}),
            headers={"Access-Control-Allow-Origin": "*"}, content_type="application/json")

    return Response(status=201, mimetype="application/json")
    
@app.route("/push_message/",methods=['POST'])
def push_message():
    message = request.form.get('message')
    print(request, message)
    try:
        db.add_message_to_queue(message)
        return Response(status=200)
    except Exception as e:
        print("error",e)
        return Response(status=403)

@app.route("/test/",methods=['POST'])
def push_v1():
    print("is_json",request.is_json)

    if not request.json or not request.json.get('sub_token'):
        return jsonify({'failed':1})

    print("request.json",request.json)

    sub_token = request.json.get('sub_token')
    message = request.json.get('message')
    try:
        token = json.loads(sub_token)
        notifier.send_web_push(token, message)
        return jsonify({'success':1})
    except Exception as e:
        print("error",e)
        return jsonify({'failed':str(e)}) 

def run_scheduler():

    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":

    schedule.every(SCHEDULE_EVERY_MINUTES).minutes.do(notifier.massive_push)

    threading.Thread(target=run_scheduler).start()

    app.run(host=WEB_SETTINGS['host'],
            port=WEB_SETTINGS['port'],
            debug=WEB_SETTINGS['debug'],
            use_reloader=WEB_SETTINGS['use_reloader'])
    