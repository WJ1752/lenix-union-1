import nonebot
from nonebot.adapters.onebot.v11 import Adapter

nonebot.init(command_start={"/"})  # 只认带斜杠的指令，裸写不响应
driver = nonebot.get_driver()
driver.register_adapter(Adapter)

nonebot.load_plugins("nonebot_plugins")

if __name__ == "__main__":
    nonebot.run()
