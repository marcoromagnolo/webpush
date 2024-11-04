'use strict';

const SERVICE_WORKER_SCRIPT = "static/sw.js";
const NOTIFY_ENDPOINT = "http://localhost:8080/subscription/";

function urlB64ToUint8Array(base64String) {
	const padding = '='.repeat((4 - base64String.length % 4) % 4);
	const base64 = (base64String + padding)
		.replace(/\-/g, '+')
		.replace(/_/g, '/');

	const rawData = window.atob(base64);
	const outputArray = new Uint8Array(rawData.length);

	for (let i = 0; i < rawData.length; ++i) {
		outputArray[i] = rawData.charCodeAt(i);
	}
	return outputArray;
}

function subscription() {
    const applicationServerPublicKey = localStorage.getItem('applicationServerPublicKey');
    if (applicationServerPublicKey) {
        const applicationServerKey = urlB64ToUint8Array(applicationServerPublicKey);

        navigator.serviceWorker.getRegistration(SERVICE_WORKER_SCRIPT).then((serviceWorkerRegistration) => {

            serviceWorkerRegistration.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: applicationServerKey
            })
            .then(function(data) {
                const storedToken = localStorage.getItem('subscription_token');
                // Update subscription only if some changed
                if (storedToken !== JSON.stringify(data)) {

                    console.log('User send subscribtion: ', data);
                    fetch(NOTIFY_ENDPOINT, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json; charset=utf-8'
                        },
                        body: JSON.stringify({
                            'subscription_token': data
                        })
                    })
                    .then(response => {
                        if (!response.ok) {
                            throw new Error(`HTTP error! Status: ${response.status}`);
                        }
                        return response.json();  // Parse JSON response
                    })
                    .then(json => {
                        localStorage.setItem('subscription_token',JSON.stringify(data));
                        console.log("Subscription completed: ", json);
                    })
                    .catch(error => {
                        console.log("Subscribtion error", error);
                    });
                }
            }).catch(function(err) {
                console.log('Failed to subscribe the user: ', err);
            });
        });
    }
}

if ('serviceWorker' in navigator && 'PushManager' in window) {
	console.log('Service Worker and Push is supported');

    navigator.serviceWorker.register(SERVICE_WORKER_SCRIPT)
    .then((serviceWorkerRegistration) => {
        console.log('Service Worker is registered', serviceWorkerRegistration);
        serviceWorkerRegistration.pushManager.getSubscription().then(
            (subscription) => {
                if (subscription === null) {
                    console.log('Get subscription public key');
                    fetch(NOTIFY_ENDPOINT, {
                        method: 'GET',
                        headers: {
                            'Content-Type': 'application/json; charset=utf-8'
                        }
                    })
                    .then(response => {
                        if (!response.ok) {
                            throw new Error(`HTTP error! Status: ${response.status}`);
                        }
                        return response.json();
                    })
                    .then(data => {
                        console.log('User received subscribtion data: ', JSON.stringify(data));
                        localStorage.setItem('applicationServerPublicKey', data.public_key);
                    })
                    .catch(error => {
                        console.log("error", error);
                    });
                }
            },
            (error) => {
                console.error(error);
            },
        );
    });
}