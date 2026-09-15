import os
import requests
import logging

logger = logging.getLogger()


def check_integer_format(text: str) -> bool:
    """
    Check that the text is a positive integer
    :param text: Text entered by the user in a tk.Entry widget
    :return:
    """
    if text == "":
        return True

    if all(x in "0123456789" for x in text):
        try:
            int(text)
            return True
        except ValueError:
            return False
    else:
        return False


def check_float_format(text: str) -> bool:
    """
    Check that the text is a positive floating number
    :param text: Text entered by the user in a tk.Entry widget
    :return:
    """
    if text == "":
        return True

    if all(x in "0123456789." for x in text) and text.count(".") <= 1:
        try:
            float(text)
            return True
        except ValueError:
            return False
    else:
        return False


def send_notification(message: str):
    """
    Sends a push notification to your phone via a Discord Webhook.
    """
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")

    if not webhook_url:
        # Fails silently if you haven't set up the URL yet
        return

    data = {
        "content": message,
        "username": "Binance Bot Alert"
    }

    try:
        # Timeout set to 3 seconds so it never freezes your bot if Discord is down
        requests.post(webhook_url, json=data, timeout=3)
    except Exception as e:
        logger.error(f"Failed to send webhook notification: {e}")