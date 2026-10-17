# AI 智能答题助手（一体化嵌入浏览器版）

基于 PySide6 + QWebEngineView，将浏览器直接嵌入程序窗口。

## 功能
- 左侧嵌入浏览器，直接打开题库页
- 扫描页面题目（复用 page_bridge.js）
- AI 答题（支持超时、分段）
- 自动勾选答案到页面
- 复制提示词+题目（可分段导出）
- 支持 DeepSeek / OpenAI 兼容 / 本地 LM Studio / llama-server

## 运行
```bash
pip install -r requirements.txt
python main.py
```

## 打包成 exe（Windows）
```bash
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --windowed --name AI答题助手 ^
  --add-data "inject;inject" main.py
```

生成的 exe 与 inject 目录需放在一起，或使用 --add-data 打进去。
