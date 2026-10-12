# 章映

把连载小说做成可以播放的一集影像：导入章节、拆成带角色的台词、为每个角色配音、按场景画插画，再把画面、声音和字幕合成 MP4。

界面和这份说明都是简体中文。默认是离线演示，不需要任何 API 密钥。

## 流水线

1. **导入**。粘贴正文，或上传 `.txt` / `.md`。单独成行的「第X章」「第X集」「第X回」以及 Markdown 标题会切成一集。编码支持 UTF-8 和 GB18030。
2. **剧本**。文本模型把每一集拆成有顺序的台词，说话人是「旁白」或角色名，并给角色一份稳定的外观描述。台词、说话人和音色都可以改。
3. **配音**。每个角色绑定一个 `voice_id`。语音提供方按句合成，再拼接成一集。句与句之间留一点静音，字幕时间跟每句音频对齐。
4. **画面**。文本模型把一集分成连续场景，提示词会重复角色外观。图像提供方按场景出图。
5. **成片**。ffmpeg 用缓慢的推拉和平移（Ken Burns）把场景图铺成视频，叠上整集音频和烧录字幕。

长任务写在 SQLite 里，由进程内的后台线程按顺序执行。测试和 `python -m app.demo` 可以改成同步执行。

## 架构

```
浏览器（frontend/）
    │
    ▼
FastAPI（app/main.py，app/api/）
    │
    ├── SQLite：作品、角色、分集、台词、场景、任务
    ├── 本地文件：data/novels/... 下的 wav、png、srt、mp4
    └── 提供方
         ├── 文本  mock | openai（兼容 Chat Completions）
         ├── 语音  mock | mosi（POST /v1/audio/speech/speakers）
         └── 图像  mock | openai（兼容 images/generations）
```

| 目录 | 作用 |
| --- | --- |
| `app/services/importer.py` | 章节切分 |
| `app/services/heuristics.py` | 离线拆词和分镜 |
| `app/providers/` | 文本、语音、图像接口和实现 |
| `app/services/pipeline.py` | 四步流水线和一键成片 |
| `app/services/video.py` | ffmpeg 画面、字幕、封装 |
| `frontend/` | 工作室界面 |
| `samples/rain-night.txt` | 两集示例《雨夜来信》 |

新增一个语音或图像厂商时，实现对应提供方并在 `app/providers/factory.py` 注册，用环境变量选中即可。

## 本地运行

需要 Python 3.12、ffmpeg，以及一种中文字体（Debian/Ubuntu 可用 `fonts-wqy-microhei`，烧录字幕要用到）。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 <http://localhost:8000> 。点「载入示例」，进入第一集，再点「一键成片」。

只在命令行里生成示例视频：

```bash
pip install -r requirements-dev.txt
python -m app.demo
```

成片在 `data/demo/` 下面，文件名是 `episode.mp4`。

## Docker

```bash
cp .env.example .env
docker compose up --build
```

服务在 <http://localhost:8000> 。数据库和生成文件放在名为 `studio-data` 的卷里。没写 `.env` 时，Compose 会按离线演示启动。

## 测试

```bash
pip install -r requirements-dev.txt
pytest
```

`tests/test_api.py` 会在离线模式下把示例第一集跑成带音轨的 MP4。`tests/test_mosi.py` 用假 HTTP 检查 MOSI 请求体，不会访问外网。

## 环境变量

完整模板在 `.env.example`。

| 变量 | 作用 |
| --- | --- |
| `APP_MODE` | `mock`（默认）或 `live`。mock 不访问外部 API。 |
| `LLM_PROVIDER` | 空则 mock 用 `mock`、live 用 `openai`。也可填 `mock` 或 `openai`。 |
| `TTS_PROVIDER` | 空则 mock 用 `mock`、live 用 `mosi`。 |
| `IMAGE_PROVIDER` | 空则 mock 用 `mock`、live 用 `openai`。 |
| `DATABASE_URL` | 默认 `sqlite:///./data/studio.db` |
| `DATA_DIR` | 生成文件目录，默认 `./data` |
| `JOBS_INLINE` | `true` 时接口等任务结束再返回。日常保持 `false`。 |
| `OPENAI_API_KEY` | 文本模型密钥。OpenAI 兼容接口。 |
| `OPENAI_BASE_URL` | 默认 `https://api.openai.com/v1` |
| `LLM_MODEL` | 默认 `gpt-4o-mini` |
| `IMAGE_API_KEY` | 图像密钥。只放在环境变量里，不要提交。 |
| `IMAGE_BASE_URL` | 默认 `https://api.aicoming.top/v1`，OpenAI 兼容的 images/generations。 |
| `IMAGE_MODEL` | 默认 `gpt-image-2.5-flare`。备选 `gpt-image-2-2k`。 |
| `IMAGE_ASPECT` | 默认 `16:9`。竖屏用 `9:16`。尺寸见下方图像一节。 |
| `IMAGE_SIZE` | 留空则按画幅映射。填写后覆盖映射。 |
| `IMAGE_TIMEOUT_SEC` | 单张超时，默认 300，建议 180–600。 |
| `IMAGE_GAP_SEC` | 两张图之间的间隔，默认 12。mock 不等待。 |
| `IMAGE_MAX_ATTEMPTS` / `IMAGE_RETRY_WAIT_SEC` | 失败或超时最多 6 次，间隔默认 48 秒。 |
| `MOSI_API_KEY` | MOSI 密钥。旧名 `MOSS_API_KEY` 仍可用。 |
| `MOSS_BASE_URL` | 默认 `https://api.mosi.cn` |
| `MOSS_MODEL` | 默认 `moss-ttsd-1.0` |
| `MOSS_RESPONSE_FORMAT` | 默认 `wav`。也可用文档里的 mp3、pcm、flac、aac、opus。 |
| `MOSS_DELIVERY_METHOD` | `url`（默认，JSON 里带音频地址）或 `audio`（直接返回音频）。 |
| `MOSS_ASYNC` | `auto`（长句改走异步任务）、`always`、`never` |
| `VIDEO_WIDTH` / `VIDEO_HEIGHT` / `VIDEO_FPS` | 成片尺寸和帧率，默认 1280×720、24fps |

真实模式至少需要：

- 拆剧本和分镜：`OPENAI_API_KEY`（或兼容服务的密钥和 `OPENAI_BASE_URL`）
- 配音：`MOSI_API_KEY`。音色从 `GET /v1/audio/voices` 拉取，旁白和每个角色各选一把，也可以粘贴 `voice_id`。
- 插画：`IMAGE_API_KEY`，接口是 `IMAGE_BASE_URL` 上的 images/generations。

可以只把某一段换成真实接口。例如保持 `APP_MODE=mock`，再设 `TTS_PROVIDER=mosi` 和 `MOSI_API_KEY`，剧本和画面仍走离线，只有配音打到 MOSI。

## MOSI 多人语音

实现遵循 [多人语音生成](https://platform.mosi.cn/docs/scenarios/dialogue)：

- `POST https://api.mosi.cn/v1/audio/speech/speakers`
- 请求头：`Authorization: Bearer $MOSI_API_KEY`、`Platform: moss-platform`
- 模型：`moss-ttsd-1.0`
- `speakers[]` 给出说话人 id 和 `voice_id`
- `segments[]` 按顺序给出 `speaker` 和 `text`
- `delivery_method=url` 时读取响应里的 `url`；`audio` 时直接保存音频
- 请求体带 `async=true` 时，用返回的 `task_id` 轮询 `GET /v1/audio/tasks/{task_id}`，成功后下载 `url`

每一句单独作为一段提交，是为了拿到这一句的时长，字幕才能对准。小说里的角色往往超过两位，而这个模型面向双人对话；逐句调用只带当前这一个说话人，再由 ffmpeg 拼成一集。同一集里两句之间默认停 1.5 秒（`MOSS_GAP_SEC`）。429、5xx 和超时会重试，最多 `MOSS_MAX_ATTEMPTS` 次（默认 4）；响应里有 `Retry-After` 就按它等，否则等 `MOSS_RETRY_WAIT_SEC`（默认 8 秒）。

音色在分集工作台右侧的「音色设置」里选。旁白和每个角色各有一把音色，可以试听，也可以粘贴 `voice_id`。每一句旁边的「重配」只重做这一句，然后重新拼接整集音频；成片会作废，需要再合成视频。密钥只放在环境变量里，不要写进前端。

## 图像

`IMAGE_PROVIDER=openai` 调用 `{IMAGE_BASE_URL}/images/generations`。默认地址是 `https://api.aicoming.top/v1`，默认模型 `gpt-image-2.5-flare`，也可以改成 `gpt-image-2-2k`。

画幅和尺寸：

| 画幅 | 尺寸 |
| --- | --- |
| 1:1 | 1024x1024 |
| 16:9 | 1792x1024 |
| 9:16 | 1024x1792 |
| 4:3 | 1792x1344 |
| 3:4 | 1344x1792 |

成片默认 16:9。竖屏把 `IMAGE_ASPECT` 设为 `9:16`，并改 `VIDEO_WIDTH` / `VIDEO_HEIGHT`。`IMAGE_SIZE` 留空时用上表；写了就覆盖画幅。

同一集的图串行生成，两张之间默认隔 12 秒。单次超时默认 300 秒。失败或超时最多试 6 次，两次之间默认等 48 秒。

最终提示词由四段拼成：这一镜的画面、整部作品可编辑的 STYLE、每个角色的外观，以及共用的 COMMON 约束（构图完整，不要水印、标志、签名，不要无关文字和乱码，不要元数据和页脚）。STYLE 在作品页的「画面风格」里改。图片写在 `data/novels/.../scenes/`，接口同时返回本地路径和上游给的 `source_url`。
