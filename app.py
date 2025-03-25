import logg
import json
import schedule, time
import threading
import notifier
import db
import pidman
from settings import WEB_SETTINGS
from datetime import datetime
from flask import request, Response, render_template, jsonify, Flask
from flask_cors import CORS

pidman.add_pid_file("webpush.pid")

logger = logg.create_logger('app')

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
            logger.info(f"{request}")
            return Response(response=json.dumps({"public_key": notifier.VAPID_PUBLIC_KEY}),
                headers={"Access-Control-Allow-Origin": "*"}, content_type="application/json")
        
        if request.method == "POST" and request.json:
            logger.info(f"{request}, {request.json}")
            subscription_token = request.json.get("subscription_token")
            subscription_token["expiration_time"] = subscription_token["expirationTime"]
            subscription_token["keys_p256dh"] = subscription_token["keys"]["p256dh"]
            subscription_token["keys_auth"] = subscription_token["keys"]["auth"]
            db.add_subscriber(subscription_token)
            return Response(response=json.dumps({'success':1}),
                headers={"Access-Control-Allow-Origin": "*"}, content_type="application/json")
        
    except Exception as e:
        logger.error(e)

    return Response(status=201, mimetype="application/json")
    
@app.route("/push_message/",methods=['POST'])
def push_message():
    """
        POST add message to queue
    """
    data = request.json
    logger.info(f"{request}, {data}")

    try:
        db.add_message(data["title"], json.dumps(data["options"]))
        return Response(status=200)
    except Exception as e:
        logger.error(e)

    return Response(status=403)

def run_scheduler():
    logger.info("Start scheduler for pushing message")
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":

    logger.info(f"Configure scheduler.")
    schedules = db.get_schedules()
    for sched in schedules:
        day = sched['day']
        hhmm = f"{sched['hour']:02}:{sched['minute']:02}"
        if day == 1:
            schedule.every().monday.at(hhmm).do(notifier.push_last_message, sched)
        elif day == 2:
            schedule.every().tuesday.at(hhmm).do(notifier.push_last_message, sched)
        elif day == 3:
            schedule.every().wednesday.at(hhmm).do(notifier.push_last_message, sched)
        elif day == 4:
            schedule.every().thursday.at(hhmm).do(notifier.push_last_message, sched)
        elif day == 5:
            schedule.every().friday.at(hhmm).do(notifier.push_last_message, sched)
        elif day == 6:
            schedule.every().saturday.at(hhmm).do(notifier.push_last_message, sched)
        elif day == 7:
            schedule.every().sunday.at(hhmm).do(notifier.push_last_message, sched)

    next_job = min(schedule.jobs, key=lambda job: job.next_run)
    logger.info("Next job run at: " + str(next_job.next_run))
    threading.Thread(target=run_scheduler).start()

    app.run(host=WEB_SETTINGS['host'],
            port=WEB_SETTINGS['port'],
            debug=WEB_SETTINGS['debug'],
            use_reloader=WEB_SETTINGS['use_reloader'])
    
