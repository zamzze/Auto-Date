from driver import driver
from lm_client import ask_llm
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
    time.sleep(10)
