# Gemini 候选消费验收：访问失败待复测

用户回传：START_HERE.md 可读，其余依赖返回 PERMISSION_DENIED；不判为验收通过。

已通过本地 Drive API 比较入口、INDEX、2024-2025 赛季、validation 和父目录：同一所有者及权限集合、同一候选目录，inheritedPermissionsDisabled=false，文件可下载。未修改共享权限。

尚不能证明 Gemini 使用相同账号/授权范围，也不能从 Gemini 回复单独确定底层拒绝原因。Markdown 文件 MIME 有 text/markdown 与 text/plain 差异，但无证据表明这是原因；暂不改写文件类型。

下一步对照：在同一个 Gemini 任务中直接提供 INDEX 和赛季文件链接，请其通过 Drive 读取。若直接链接成功而入口内跳转失败，记录为入口依赖发现/访问问题；若仍失败，用 Gemini 的“从 Drive 添加”选择同一文件再试，并核对浏览器使用 Gemini 同一账号是否能打开。手工添加成功只证明该消费方式可行，不等于自动递归读取已通过。

直接链接：
- https://drive.google.com/file/d/1OS--U2siBcbVuq_RLj5seSJRx8P7JjJ2/view
- https://drive.google.com/file/d/10wmOoMYdIT_GxBK68QN2dphHrnfw4WOj/view

生产仍为 v1.1 FINAL。全量生产切换和 v1.5.0 tag 继续等待消费验收。
