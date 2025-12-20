def send_reply(driver, text):
    input_box = driver.find_element(
        "id", "com.meyo:id/message_input"
    )
    input_box.send_keys(text)

    send_button = driver.find_element(
        "id", "com.meyo:id/send_button"
    )
    send_button.click()
