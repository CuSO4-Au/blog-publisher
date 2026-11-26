# Blog Publisher

把本地 Markdown、配图和文章信息传到自建 WordPress。可以先在本地预览，再新建或更新草稿。公开发布有独立命令，适合接在 Codex 或其他写作工具之后。博客使用哪家主机可以由使用者配置。

从项目记录开始写文章时，使用 [写作提示词](prompts/project-to-blog.md)，提纲确认、正文确认、草稿上传和正式发布按 [工作流](WORKFLOW.md) 执行。这份工作流供 Codex 或其他写作助手读取，上传动作由本项目的命令行工具完成。

需要 Python 3.11 或更高版本。Windows PowerShell 可以这样安装。

```powershell
git clone https://github.com/CuSO4-Au/blog-publisher.git
cd blog-publisher
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
$publisher = ".\.venv\Scripts\blog-publisher.exe"
& $publisher preview examples/hello.md
```

macOS 或 Linux 下，用 `.venv/bin/python -m pip install .` 安装，再运行 `.venv/bin/blog-publisher preview examples/hello.md`。

在启用了 HTTPS 的 WordPress 后台，从「用户 → 个人资料 → 应用密码」创建一个专用应用密码。使用有相应权限的 WordPress 账号。草稿需要文章编辑权限，有配图时还需要媒体上传权限，公开发布需要发布权限。

```powershell
$env:WP_PUBLISHER_URL = "https://blog.example.com"
$env:WP_PUBLISHER_USER = "your-wordpress-username"
& $publisher check
& $publisher draft examples/hello.md
```

默认在终端里隐式输入应用密码。自动化调用可以从凭据管理器把密码注入 `WP_PUBLISHER_APP_PASSWORD`。示例中的站点地址请换成自己的 WordPress 安装地址。如果主机只开放 `/wp-json/`，设置 `WP_PUBLISHER_REST_MODE=pretty`。默认使用 `?rest_route=`，支持使用朴素固定链接的 WordPress。HTTP 只用于本机回环测试。

文章可以直接从一级标题开始，也可以加 YAML 文章信息。示例展示了标题、别名和摘要。可选 `categories` 和 `tags` 填已有词条的别名列表，例如 `tags: [wordpress]`。`cover: images/cover.webp` 可指定特色图片。正文配图可用 `![说明](images/picture.png)`，路径相对于 Markdown 文件。工具支持 JPEG、PNG、GIF、WebP 和 AVIF，实际格式限制由站点决定。外部 HTTP(S) 图片保留原链接。预览返回 JSON，其中包含 HTML 和待上传的文件列表。

草稿创建后会返回文章 ID 和后台编辑地址，并在稿件旁保存 `文章文件名.publisher.json`。记录按 WordPress 站点分别保存草稿编号、最近一次上传结果和操作时间。修改本地文件后，重复执行同一条 `draft` 命令会更新已记录的草稿。记录只有少量 JSON 数据。

`record` 可以离线查看最近一次上传记录，当前远程状态可用 `status ID` 查询。第一次关联以前已经上传的文章时，使用 `--post-id` 指定原草稿。以下示例用 `42` 代替实际 ID。

```powershell
& $publisher draft examples/hello.md
& $publisher record examples/hello.md
& $publisher draft older-article.md --post-id 42
& $publisher status 42
& $publisher publish 42 --confirm
```

更新时，省略分类、标签、摘要或封面则沿用草稿已有值。每次更新会同步标题和正文，建议先确认后台没有人正在编辑这份稿件。

有配图时，工具先建立草稿，再把编号保存到本地记录，然后上传图片。中途失败后，重复执行原命令即可继续更新这个编号，同一草稿会复用已上传的同一图片。如果创建请求中断、工具没有拿到编号，会提示先查看后台，再用 `--post-id` 关联，避免再次创建草稿。

同一份稿件的并行上传通过本地文件锁协调，进程退出会释放锁。记录文件原子替换，发现记录损坏会提示检查。已发布的文章继续通过草稿状态检查保护。WordPress 媒体库里的文件可能直接通过图片地址访问，即使文章仍是草稿。

请保留稿件旁的 `.publisher.json` 文件，移动或改名时一起带上。项目的 Git 忽略规则已包含任务记录及 `.publisher.lock` 锁文件；如果稿件放在其他仓库，也请在那里添加这些忽略规则。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

本项目使用 [MIT 许可证](LICENSE)。
