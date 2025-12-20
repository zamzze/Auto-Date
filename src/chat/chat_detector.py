def get_unread_chats(driver):
    return driver.find_elements(
        "xpath", "//android.view.View[contains(@content-desc, 'unread')]"
    )
