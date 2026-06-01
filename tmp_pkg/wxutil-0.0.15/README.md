# wxutil

**微信3.x图片解密**

```python
import pathlib
from wxutil.dat import find_key, decrypt_dat

xor_key, aes_key = find_key(pathlib.Path(r"C:\Users\<username>\Documents\WeChat Files\<wxid>"), version=3)

data = decrypt_dat(
    r"C:\Users\<username>\Documents\WeChat Files\<wxid>\FileStorage\Image\2024-11\00f0395964ec76d2406f15cc69c4e566.dat",
    xor_key)
with open("00f0395964ec76d2406f15cc69c4e566.png", "wb") as f:
    f.write(data)
```

**微信3.x消息监听**

```python
from wxutil.db.v3 import WeChatDB, ALL_MESSAGE

wechat_db = WeChatDB()


@wechat_db.handle(ALL_MESSAGE)
def _(wechat_db, event):
    print(event)


wechat_db.run()
```

**微信4.x消息监听**

```python
from wxutil.db.v4 import WeChatDB, ALL_MESSAGE
from wxutil.utils import get_wechat4_key
import psutil


def get_wechat_pid():
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["name"].lower() == "weixin.exe":
                return proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return None


key = get_wechat4_key()

pid = get_wechat_pid()
if pid is None:
    raise Exception("未找到微信进程")

wechat_db = WeChatDB(
    pid=pid,
    key=key,
    data_dir=r"C:\Users\<username>\xwechat_files\<wxid>"
)


@wechat_db.handle(ALL_MESSAGE)
def _(wechat_db, event):
    print(event)


wechat_db.run()
```