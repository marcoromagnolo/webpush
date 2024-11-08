import logging
import json, os
import schedule, time
import threading
import notifier
import db
from settings import WEB_SETTINGS, SCHEDULE_EVERY_MINUTES
from datetime import datetime

from flask import request, Response, render_template, jsonify, Flask
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": WEB_SETTINGS["cors_origins"]}})
app.config['SECRET_KEY'] = '9OLWxND4o83j4K4iuopO'

@app.route("/subscription/", methods=["GET", "POST"])
def subscription():
    """
        POST creates a subscription
        GET returns vapid public key which clients uses to send around push notification
    """

    try:
        if request.method == "GET":
            logging.info(f"{request}")
            return Response(response=json.dumps({"public_key": notifier.VAPID_PUBLIC_KEY}),
                headers={"Access-Control-Allow-Origin": "*"}, content_type="application/json")
        
        if request.method == "POST" and request.json:
            logging.info(f"{request}, {request.json}")
            subscription_token = request.json.get("subscription_token")
            subscription_token["expiration_time"] = subscription_token["expirationTime"]
            subscription_token["keys_p256dh"] = subscription_token["keys"]["p256dh"]
            subscription_token["keys_auth"] = subscription_token["keys"]["auth"]
            db.add_subscriber(subscription_token)
            return Response(response=json.dumps({'success':1}),
                headers={"Access-Control-Allow-Origin": "*"}, content_type="application/json")
        
    except Exception as e:
        logging.error(e)

    return Response(status=201, mimetype="application/json")
    
@app.route("/push_message/",methods=['POST'])
def push_message():
    """
        POST add message to queue
    """
    data = request.json
    logging.info(f"{request}, {data}")

    try:
        db.add_message(data["title"], json.dumps(data["options"]))
        return Response(status=200)
    except Exception as e:
        logging.error(e)

    return Response(status=403)

def run_scheduler():
    logging.info("Start scheduler for massive push")
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":

    logging.info(f"Configure scheduler for running every {SCHEDULE_EVERY_MINUTES} minutes.")
    schedule.every(SCHEDULE_EVERY_MINUTES).minutes.do(notifier.massive_push)

    threading.Thread(target=run_scheduler).start()

    app.run(host=WEB_SETTINGS['host'],
            port=WEB_SETTINGS['port'],
            debug=WEB_SETTINGS['debug'],
            use_reloader=WEB_SETTINGS['use_reloader'])
    
