# 从项目记录到 WordPress 草稿

处理项目记录写作、现有稿件上传、草稿更新或用户确认发布时，按本文选择对应阶段。写作使用 [项目记录转博客提示词](prompts/project-to-blog.md)，安装和站点配置见 [README](README.md)。

当前发布目标是自建 WordPress，站点和凭据由使用者配置。工具通过 WordPress REST API 上传文章和图片。

## 开始位置

沿用当前对话中已经给出的确认与授权。已有明确确认的提纲就继续写正文，已有确认稿或明确的上传指令就进入本地检查。用户提供修订稿时，以最新版本为准。

1. **提纲**。根据提供的项目记录整理简短提纲，缺少的事实集中询问。提纲获得确认后进入正文阶段。
2. **正文**。输出标题、摘要、英文网址别名和 Markdown 正文，供用户审阅。配图选用用户提供的项目素材，引用路径相对于稿件目录。分类和标签填写已核对的站点词条别名。
3. **本地检查**。正文得到确认后，保存 Markdown 并运行下面的预览命令。已有稿件沿用原路径，新稿默认可放在本项目的 `.local/drafts/`，向用户说明实际位置。文章信息格式参考 [示例稿件](examples/hello.md)。

   ```powershell
   blog-publisher preview path/to/article.md
   ```

   核对输出的标题、代码块语言、HTML 和待上传文件列表。格式调整在交付时说明，文字修改遵循用户给定范围。本地检查通过即可进入已经获准的草稿上传。

4. **草稿上传**。首次配置或切换站点、凭据时，运行 `blog-publisher check` 核对连接与权限。应用密码通过终端隐藏输入或环境变量提供。确认目标为本次指定站点后，执行上传。

   ```powershell
   blog-publisher draft path/to/article.md
   ```

   工具会按本地记录复用当前站点的原草稿。接入此前上传的旧稿时，用用户提供或已验证记录中的真实编号关联。

   ```powershell
   blog-publisher draft path/to/article.md --post-id POST_ID
   ```

   `POST_ID` 替换为实际草稿编号。成功返回 `status` 为 `draft` 后，向用户交付编号、`edit_url` 和本地稿件路径。

5. **公开发布**。用户明确确认发布这篇文章后，使用对应编号执行发布。此前已取得的同一发布授权继续有效。

   ```powershell
   blog-publisher publish POST_ID --confirm
   ```

   成功返回 `status` 为 `publish` 后，交付返回的公开文章地址。用户要求保存草稿时，以第 4 步的结果结束。

## 更新和失败处理

更新已关联的草稿时，修改原 Markdown，再运行相同的 `draft` 命令。本地 `.publisher.json` 和 `.publisher.lock` 文件用于记录与协调上传，随稿件保存在本机。

```powershell
blog-publisher record path/to/article.md
blog-publisher status POST_ID
```

`record` 显示本地最近上传记录，`status` 查询 WordPress 当前状态。根据实际命令结果报告状态。

已取得编号的上传中断后，可以继续运行原命令。创建请求中断且编号未知时，先在 WordPress 核对稿件，确认实际编号后用 `--post-id` 关联。记录损坏或目标文章已公开发布时，按错误提示核对现有记录与文章。
