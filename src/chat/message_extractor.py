def get_last_message(driver):
    messages = driver.find_elements(
        "id", "com.meyo:id/message_text"
    )
    return messages[-1].text
