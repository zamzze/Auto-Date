from src.appium.driver import driver
from src.llm.lm_client import ask_llm
from src.chat.chat_detector import get_unread_chats
from src.chat.message_extractor import get_last_message
from src.chat.message_sender import send_reply

import time

def process_chats():
    unread_chats = get_unread_chats(driver)

    for chat in unread_chats:
        chat.click()
        time.sleep(1)

        incoming = get_last_message(driver)
        response = ask_llm(incoming)

        send_reply(driver, response)

        driver.back()
        time.sleep(1)
while True:
    process_chats()
    time.sleep(20)
