import logging
from urllib.parse import urlparse
import json, os
from datetime import datetime
import requests
from pywebpush import webpush, WebPushException
import db
from settings import VAPID_CLAIMS_SUB_MAILTO, SCHEDULE_EVERY_MINUTES

DER_BASE64_ENCODED_PRIVATE_KEY_FILE_PATH = os.path.join(os.getcwd(),"private_key.txt")
DER_BASE64_ENCODED_PUBLIC_KEY_FILE_PATH = os.path.join(os.getcwd(),"public_key.txt")

VAPID_PRIVATE_KEY = open(DER_BASE64_ENCODED_PRIVATE_KEY_FILE_PATH, "r+").readline().strip("\n")
VAPID_PUBLIC_KEY = open(DER_BASE64_ENCODED_PUBLIC_KEY_FILE_PATH, "r+").read().strip("\n")

VAPID_CLAIMS = {
"sub": f"mailto:{VAPID_CLAIMS_SUB_MAILTO}"
}

def send_web_push(subscription_information, message_body):
    parsed_url = urlparse(subscription_information.get("endpoint"))
    if "google.com" in parsed_url.netloc:
        VAPID_CLAIMS["aud"] = f"{parsed_url.scheme}://{parsed_url.netloc}"
    return webpush(
        subscription_info=subscription_information,
        data=message_body,
        vapid_private_key=VAPID_PRIVATE_KEY,
        vapid_claims=VAPID_CLAIMS
    )    

def massive_push():
    print("Massive Push Job starting...")
    last = db.get_last_in_queue()

    if last:
        print("Load last message from queue: ", last)
        log = {
            "message": last["message"],
            "total_subscribers": 0,
            "total_pushes": 0,
            "start_time": datetime.now(),
            "end_time": ""
        }
        
        subscribers = db.get_subscribers()
        print("There are ", len(subscribers), " subscribers")

        for subscriber in subscribers:
            log["total_subscribers"] += 1    
            token = {
                "endpoint": subscriber["endpoint"],
                "expirationTime": subscriber["expiration_time"],
                "keys": {
                    "p256dh": subscriber["keys_p256dh"],
                    "auth": subscriber["keys_auth"]
                }}
            
            try:
                print("Send push request: ", token)
                push_response = send_web_push(token, log["message"])
                print("Received push response: ", push_response)

                if push_response.status_code in (200, 201):
                    log["total_pushes"] += 1
                    
            except WebPushException:
                # remove subscriber
                print("Remove subscriber ", subscriber)
                db.remove_subscriber(subscriber)
            
        # set webpush_queue.pushed = 1
        db.set_queue_message_pushed(last["id"])

        # add a row in webpush_log
        log["end_time"] = datetime.now()
        db.add_queue_log(log)

    else:
        print("The queue is empty")

    print("Massive Push Job end.")