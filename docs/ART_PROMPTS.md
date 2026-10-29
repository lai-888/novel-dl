# 视觉素材提示词

给这个项目做配图/贴图时用。**核心原则：这是工具，不是游戏启动器。**
纹样只能用在小面积（分隔线、边角、空状态、封面缩略），不能当背景铺满。

参考图的风格可以概括为：**深墨底 + 细金线 + 几何/尘埃质感**。项目里已经用纯 CSS 实现了
底纹（颗粒）、金线（两端渐隐）、角标框（四角装饰），零图片、零请求。

只有下面这些情况才值得真的去生图：

- 书架为空时的空状态插画
- 书籍封面占位图
- README 的展示图 / 项目头图

---

## 一、底纹贴图（可平铺）

用途：需要更像「手绘/丝网印」质感时，替代 CSS 渐变。

```
seamless tileable texture, very dark warm charcoal background (#0b0a09),
faint scattered gold dust particles, extremely subtle, high contrast noise,
aged paper grain, minimalist, monochrome with single gold accent,
no text, no objects, tileable repeating pattern, 512x512, flat design
```

要点：
- 必须写 `seamless tileable` / `repeating pattern`，否则拼接有缝
- `faint` `extremely subtle` 是关键，纹样一旦显眼就毁了工具感
- 尺寸要小（512），生成后再用 CSS 平铺

---

## 二、金线分隔（小尺寸横条）

用途：章节之间的分隔装饰。项目里用 CSS 做了，这个是备选。

```
thin horizontal gold line ornament, delicate, fades out at both ends,
minimal, dark transparent background, decorative divider,
elegant gold thread with slight glow, no text, 1200x60
```

要点：
- 高度要小（60px 以内），横条比例
- `fades out at both ends` 做出两端渐隐
- 背景要 `dark transparent`，方便叠在深色页面上

---

## 三、角标边框（贴图九宫格）

用途：需要更精致的框线时，替代 CSS 伪元素。

```
minimal square border frame, thin gold line, small geometric corner ornaments,
inset corners with short diagonal marks, dark transparent background,
decorative frame only, no content inside, clean line art, 400x400
```

生成后用 `border-image` 做九宫格拉伸，或干脆只截四角做角标。

---

## 四、空状态插画

用途：书架没书时显示。

```
minimalist line art illustration, a single closed book on a dark surface,
warm charcoal background, thin gold outline, very simple, lots of negative space,
no text, centered composition, editorial illustration style, 800x600
```

要点：`lots of negative space` 和 `very simple` 是重点，不要太花。

---

## 五、封面占位

用途：书没有封面时。

```
abstract book cover placeholder, deep charcoal background with subtle gold
geometric texture, minimal, no text, vertical 2:3 aspect ratio, elegant,
single subtle gold accent line, plenty of empty space
```

---

## 六、README 项目头图

用途：GitHub 仓库顶部展示图。

```
dark editorial banner, right side shows a simple geometric gold ornament line
motif, left side mostly empty dark space for text overlay,
warm charcoal background, thin gold line accents, minimal,
technical and calm, no text, 1280x420
```

要点：**左侧留空**给标题文字，右侧放装饰。不要满铺纹样。

---

## 生成参数建议

| 场景 | 尺寸 | 备注 |
|---|---|---|
| 底纹 | 512×512 | 必须平铺 |
| 分隔线 | 1200×60 | 透明背景 |
| 角标框 | 400×400 | 透明背景 |
| 空状态 | 800×600 | 留白多 |
| 封面 | 400×600 | 2:3 |
| README 头图 | 1280×420 | 左空右饰 |

**负向提示词**（通用）：

```
text, watermark, logo, letters, typography, bright colors, neon, neon glow,
purple, blue gradient, busy, cluttered, ornate, fantasy, game UI, ornate frame,
heavy decoration, drop shadow, 3d render, plastic, glossy, low contrast
```

---

## 为什么不建议大量用生图

1. **体积**：一张背景图 200KB 起步，而这个工具的核心优势是零依赖、秒启动。
2. **一致性**：每张生图风格都会漂，而 CSS 生成的纹样永远一致、永远对齐。
3. **清晰度**：1px 金线在 2x/3x 屏上，位图会糊，CSS 的 `linear-gradient` 永远锐利。
4. **可维护**：改颜色只需改变量，改图要重新生成一批。

所以默认策略是：**能用 CSS 表达的一律用 CSS**，只在「需要插画感」的地方用生图。
