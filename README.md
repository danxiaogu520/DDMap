# 未有之梦 · 世界图志

《欧陆风云 IV》模组《未有之梦》的 1444 年 11 月 11 日政治地图。拖动平移，滚轮或双指缩放，点击国家查看国旗、政体、统治者、外交和可用的开局背景。左上角可切换「仅推荐国」和界面语言。DD 特有的名称和背景在缺少英文译文时沿用中文。

推荐国参考 DD 的开局检测事件 `DD_common.2`。威尼斯因有任务而免于该事件的提示，地图将其标为「非推荐国」。地图仅标记该日期拥有领土的国家。档案反映选国时的历史数据，进入游戏后由脚本触发的变化另计。

[打开地图](https://danxiaogu520.github.io/DDMap/)

## 本地预览

在项目根目录运行以下命令，然后打开 [本地地图](http://127.0.0.1:8766/)。

```powershell
python -m http.server 8766 --bind 127.0.0.1 --directory dist
```

`dist/` 包含完整的静态网页、地图数据、字体和图片。推送到 `main` 后，GitHub Actions 会将 `dist/` 发布到 GitHub Pages。

## 更新数据

从项目根目录运行下列命令。构建读取本机的 EU4、DD（创意工坊 ID `3416098047`）及其两个汉化依赖。已安装 DD 英文补丁（`3421928658`）时，构建会导入可用的国家档案和背景译文。

```powershell
python -m pip install Pillow NumPy opencv-python 'Shapely>=2.1' fonttools
python scripts\build_demo.py
```

Steam 库路径可通过 `--steamapps` 指定。DD 和英文补丁的路径可分别通过 `--mod` 和 `--english-mod` 指定。地图和档案样式沿用 EDGMap，历史资料和背景来自 EU4 与 DD。
