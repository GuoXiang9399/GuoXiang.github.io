# 首页视觉重构计划

## Context
当前首页 about.md 仅两行纯文本，缺乏视觉冲击力。需要利用河南大学校园风光图片，重新设计为一个美观的学术实验室首页。

## 方案概述
使用 `splash` 布局（全宽、无侧边栏）+ hero overlay 横幅 + 自定义 CSS，构建包含以下区块的首页：

1. **Hero 横幅（75vh）** — 明伦校区全景图 + 半透明遮罩 + 实验室名称 + CTA 按钮
2. **研究方向** — 3 列图文卡片，每张配校园图
3. **实验室简介** — 左图右文布局
4. **校园风光** — 2x2 图片网格
5. **快速导航** — 4 列图标卡片，链接到 Team/Publications/Teaching/Tools

## 修改文件清单

### 1. `_pages/about.md` — 重写 front matter 和内容
- 改 `layout: splash`
- 添加 `header.overlay_image`、`overlay_filter`、`caption`、`cta_url`/`cta_label`
- 设置 `title` 和 `excerpt`
- 正文使用 HTML section 标签构建各区块

### 2. `_sass/layout/_homepage.scss` — 新建
- Hero overlay 高度增强（65-75vh）
- 研究方向卡片网格（3列）
- 实验室简介（左图右文 flex 布局）
- 校园风光（2x2 图片网格）
- 快速导航（4列图标卡片）
- 响应式断点适配
- splash 内容区全宽适配

### 3. `assets/css/main.scss` — 添加导入
- 在 `"layout/team"` 之后添加 `"layout/homepage"`

### 4. 下载校园图片到本地（可选但推荐）
- 创建 `images/campus/` 目录
- 下载明伦/金明校区图片到本地
- 替换 about.md 中的外部 URL 为本地路径

## 图片来源
河南大学官网校园风光页面，主要使用：
- 明伦校区封面图（Hero + 研究方向 + 简介）
- 金明校区封面图（研究方向 + 校园风光）
- 其他明伦/金明校区图（校园风光网格）

## 验证
运行 `bundle exec jekyll serve` 本地预览，确认：
- Hero 图片加载且文字可读
- 各区块响应式布局正常
- 快速导航链接正确
- CTA 按钮跳转至 /team/
