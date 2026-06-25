# Skill Template — Dark Creative Agency
## cx2118 Script Weaver v9.3.0

> 整合 animate.css(82k⭐) + AOS(28k⭐) + GSAP 最佳动效模式。
> AI套模版：根据需求选区块 → 替换文案 → 拼接HTML → 底部加标注。
> 单一配色 #0a0a0f + #d0ff71。加载<3s。

---

## 一、动画库速查表（来自高星项目）

### animate.css 动画类（82.6k⭐）
```css
/* 基础入场 */
.animate__animated{animation-duration:1s;animation-fill-mode:both}
.animate__animated.animate__faster{animation-duration:.5s}
.animate__animated.animate__slower{animation-duration:1.5s}
/* 淡入 */
.animate__fadeIn{animation-name:fadeIn}
.animate__fadeInUp{animation-name:fadeInUp}
.animate__fadeInDown{animation-name:fadeInDown}
.animate__fadeInLeft{animation-name:fadeInLeft}
.animate__fadeInRight{animation-name:fadeInRight}
.animate__fadeInUpBig{animation-name:fadeInUpBig}
.animate__fadeInDownBig{animation-name:fadeInDownBig}
.animate__fadeInLeftBig{animation-name:fadeInLeftBig}
.animate__fadeInRightBig{animation-name:fadeInRightBig}
/* 翻转 */
.animate__flipInX{animation-name:flipInX}
.animate__flipInY{animation-name:flipInY}
.animate__flipOutX{animation-name:flipOutX}
/* 弹跳 */
.animate__bounceIn{animation-name:bounceIn}
.animate__bounceInUp{animation-name:bounceInUp}
.animate__bounceInDown{animation-name:bounceInDown}
.animate__bounceInLeft{animation-name:bounceInLeft}
.animate__bounceInRight{animation-name:bounceInRight}
/* 缩放 */
.animate__zoomIn{animation-name:zoomIn}
.animate__zoomInUp{animation-name:zoomInUp}
.animate__zoomInDown{animation-name:zoomInDown}
.animate__zoomInLeft{animation-name:zoomInLeft}
.animate__zoomInRight{animation-name:zoomInRight}
.animate__zoomInBig{animation-name:zoomInBig}
/* 滑入 */
.animate__slideInUp{animation-name:slideInUp}
.animate__slideInDown{animation-name:slideInDown}
.animate__slideInLeft{animation-name:slideInLeft}
.animate__slideInRight{animation-name:slideInRight}
/* 旋转 */
.animate__rotateIn{animation-name:rotateIn}
.animate__rotateInDownLeft{animation-name:rotateInDownLeft}
.animate__rotateInDownRight{animation-name:rotateInDownRight}
.animate__rotateInUpLeft{animation-name:rotateInUpLeft}
.animate__rotateInUpRight{animation-name:rotateInUpRight}
/* 特效 */
.animate__rubberBand{animation-name:rubberBand}
.animate__shakeX{animation-name:shakeX}
.animate__shakeY{animation-name:shakeY}
.animate__swing{animation-name:swing}
.animate__tada{animation-name:tada}
.animate__wobble{animation-name:wobble}
.animate__jello{animation-name:jello}
.animate__heartBeat{animation-name:heartBeat}
/* 延迟 */
.animate__delay-1{animation-delay:.1s}
.animate__delay-2{animation-delay:.2s}
.animate__delay-3{animation-delay:.3s}
.animate__delay-4{animation-delay:.4s}
.animate__delay-5{animation-delay:.5s}
.animate__delay-6{animation-delay:.6s}
.animate__delay-7{animation-delay:.7s}
.animate__delay-8{animation-delay:.8s}
```

### AOS 滚动动画（28.1k⭐）
```html
<!-- 用法：data-aos="动画名" + data-aos-delay/duration/offset -->
<div data-aos="fade-up">上浮淡入</div>
<div data-aos="fade-down">下沉淡入</div>
<div data-aos="fade-left">左侧淡入</div>
<div data-aos="fade-right">右侧淡入</div>
<div data-aos="fade-up-right">右上淡入</div>
<div data-aos="fade-up-left">左上淡入</div>
<div data-aos="fade-down-right">右下淡入</div>
<div data-aos="fade-down-left">左下淡入</div>
<div data-aos="flip-left">左侧翻转</div>
<div data-aos="flip-right">右侧翻转</div>
<div data-aos="flip-up">向上翻转</div>
<div data-aos="flip-down">向下翻转</div>
<div data-aos="zoom-in">缩放淡入</div>
<div data-aos="zoom-in-up">上浮缩放</div>
<div data-aos="zoom-in-down">下沉缩放</div>
<div data-aos="zoom-in-left">左侧缩放</div>
<div data-aos="zoom-in-right">右侧缩放</div>
<div data-aos="zoom-out">缩小淡出</div>
<div data-aos="slide-up">上滑入</div>
<div data-aos="slide-down">下滑入</div>
<div data-aos="slide-left">左滑入</div>
<div data-aos="slide-right">右滑入</div>
<!-- 自定义 -->
<div data-aos="fade-up" data-aos-delay="200" data-aos-duration="800" data-aos-offset="100" data-aos-easing="ease-out-cubic">自定义</div>
```

### 自定义keyframes（整合最佳效果）
```css
@keyframes fadeInUp{from{opacity:0;transform:translateY(40px)}to{opacity:1;transform:translateY(0)}}
@keyframes fadeInDown{from{opacity:0;transform:translateY(-40px)}to{opacity:1;transform:translateY(0)}}
@keyframes fadeInLeft{from{opacity:0;transform:translateX(-60px)}to{opacity:1;transform:translateX(0)}}
@keyframes fadeInRight{from{opacity:0;transform:translateX(60px)}to{opacity:1;transform:translateX(0)}}
@keyframes fadeInUpBig{from{opacity:0;transform:translateY(80px)}to{opacity:1;transform:translateY(0)}}
@keyframes fadeInLeftBig{from{opacity:0;transform:translateX(-120px)}to{opacity:1;transform:translateX(0)}}
@keyframes fadeInRightBig{from{opacity:0;transform:translateX(120px)}to{opacity:1;transform:translateX(0)}}
@keyframes flipInX{from{opacity:0;transform:perspective(400px) rotateX(90deg)}40%{transform:perspective(400px) rotateX(-10deg)}70%{transform:perspective(400px) rotateX(10deg)}to{opacity:1;transform:perspective(400px) rotateX(0)}}
@keyframes flipInY{from{opacity:0;transform:perspective(400px) rotateY(90deg)}40%{transform:perspective(400px) rotateY(-10deg)}70%{transform:perspective(400px) rotateY(10deg)}to{opacity:1;transform:perspective(400px) rotateY(0)}}
@keyframes bounceIn{0%{opacity:0;transform:scale(.3)}50%{opacity:1;transform:scale(1.05)}70%{transform:scale(.9)}100%{transform:scale(1)}}
@keyframes bounceInUp{0%{opacity:0;transform:translateY(40px)}60%{opacity:1;transform:translateY(-10px)}80%{transform:translateY(5px)}100%{transform:translateY(0)}}
@keyframes zoomIn{from{opacity:0;transform:scale(.5)}to{opacity:1;transform:scale(1)}}
@keyframes zoomInBig{from{opacity:0;transform:scale(.2)}to{opacity:1;transform:scale(1)}}
@keyframes slideInUp{from{transform:translateY(100%)}to{transform:translateY(0)}}
@keyframes slideInLeft{from{transform:translateX(-100%)}to{transform:translateX(0)}}
@keyframes rotateIn{from{opacity:0;transform:rotate(-200deg)}to{opacity:1;transform:rotate(0)}}
@keyframes rubberBand{0%{transform:scale(1)}30%{transform:scale(1.25,.75)}40%{transform:scale(.75,1.25)}50%{transform:scale(1.15,.85)}65%{transform:scale(.95,1.05)}75%{transform:scale(1.05,.95)}100%{transform:scale(1)}}
@keyframes shakeX{0%,100%{transform:translateX(0)}10%,30%,50%,70%,90%{transform:translateX(-5px)}20%,40%,60%,80%{transform:translateX(5px)}}
@keyframes swing{20%{transform:rotate(15deg)}40%{transform:rotate(-10deg)}60%{transform:rotate(5deg)}80%{transform:rotate(-5deg)}100%{transform:rotate(0)}}
@keyframes tada{0%,100%{transform:scale(1) rotate(0)}10%,20%{transform:scale(.9) rotate(-3deg)}30%,50%,70%,90%{transform:scale(1.1) rotate(3deg)}40%,60%,80%{transform:scale(1.1) rotate(-3deg)}}
@keyframes wobble{0%{transform:translateX(0)}15%{transform:translateX(-25px) rotate(-5deg)}30%{transform:translateX(20px) rotate(3deg)}45%{transform:translateX(-15px) rotate(-3deg)}60%{transform:translateX(10px) rotate(2deg)}75%{transform:translateX(-5px) rotate(-1deg)}100%{transform:translateX(0)}}
@keyframes heartBeat{14%{transform:scale(1.3)}28%{transform:scale(1)}42%{transform:scale(1.3)}70%{transform:scale(1)}}
@keyframes jackInTheBox{0%{opacity:0;transform:scale(.1) rotate(30deg);transform-origin:center bottom}50%{transform:scale(-95%,105%)}70%{transform:scale(1.05)}100%{opacity:1;transform:scale(1) rotate(0)}}
@keyframes hinge{0%{transform:rotate(0);transform-origin:top left}20%{transform:rotate(25deg)}40%{transform:rotate(-20deg)}60%{transform:rotate(15deg)}80%{transform:rotate(-10deg)}100%{transform:rotate(0);opacity:0}}
@keyframes rollIn{from{opacity:0;transform:translate3d(-100%,0,0) rotate(360deg)}to{opacity:1;transform:none}}
@keyframes lightSpeedInRight{from{opacity:0;transform:translate3d(100%,0,0) skewX(-30deg)}60%{opacity:1;transform:skewX(20deg)}80%{transform:skewX(-5deg)}to{transform:none}}
@keyframes backInUp{0%{opacity:0;transform:translateY(120%) scale(.7)}80%{opacity:.7;transform:translateY(0) scale(.7)}100%{opacity:1;transform:translateY(0) scale(1)}}
@keyframes backInDown{0%{opacity:0;transform:translateY(-120%) scale(.7)}80%{opacity:.7;transform:translateY(0) scale(.7)}100%{opacity:1;transform:translateY(0) scale(1)}}
@keyframes blurIn{from{opacity:0;filter:blur(20px)}to{opacity:1;filter:blur(0)}}
@keyframes typewriter{from{width:0}to{width:100%}}
@keyframes blink{50%{border-color:transparent}}
@keyframes morphShape{0%,100%{border-radius:60% 40% 30% 70%/60% 30% 70% 40%}50%{border-radius:30% 60% 70% 40%/50% 60% 30% 60%}}
@keyframes glowPulse{0%,100%{box-shadow:0 0 20px var(--accent-glow)}50%{box-shadow:0 0 40px var(--accent-glow),0 0 80px var(--accent-dim)}}
@keyframes particleBurst{0%{transform:scale(0);opacity:1}100%{transform:scale(3);opacity:0}}
@keyframes shimmer{0%{background-position:-200% center}100%{background-position:200% center}}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-15px)}}
@keyframes breathe{0%,100%{transform:scale(1);opacity:.8}50%{transform:scale(1.05);opacity:1}}
```

---

## 二、完整HTML骨架

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{TITLE}}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
/* === CSS变量 === */
:root{--bg:#0a0a0f;--surface:#111118;--card:#16161e;--text:#f0f0f5;--text-sec:#9999aa;--text-dim:rgba(255,255,255,.6);--accent:#d0ff71;--accent-glow:rgba(208,255,113,.35);--accent-dim:rgba(208,255,113,.08);--accent-border:rgba(208,255,113,.25);--border:rgba(255,255,255,.06);--border-light:rgba(255,255,255,.12);--r:12px}

/* === Reset === */
*{box-sizing:border-box;margin:0;padding:0}html{overflow-x:clip;scroll-behavior:smooth}body{background:var(--bg);color:var(--text-sec);font:16px/1.7 Inter,system-ui,sans-serif;overflow-x:clip}a{color:var(--text);text-decoration:none}img{max-width:100%;display:block}ul{list-style:none;margin:0;padding:0}::selection{background:var(--accent);color:var(--bg)}

/* === 光标 === */
*,*::before,*::after{cursor:none!important}
@media(pointer:coarse){*,*::before,*::after{cursor:auto!important}}
.cursor-dot,.cursor-ring{position:fixed;top:0;left:0;pointer-events:none;z-index:99999;border-radius:50%;transform:translate(-50%,-50%)}
.cursor-dot{width:8px;height:8px;background:var(--accent);mix-blend-mode:difference;transition:transform .15s cubic-bezier(.16,1,.3,1),opacity .3s}
.cursor-ring{width:36px;height:36px;border:1.5px solid var(--accent);opacity:.4;transition:width .4s cubic-bezier(.16,1,.3,1),height .4s,opacity .3s,border-color .3s,background .3s}
.cursor-ring.is-link{width:56px;height:56px;border-color:var(--text);opacity:.7}
.cursor-ring.is-btn{width:80px;height:80px;background:var(--accent);border-color:var(--accent);opacity:.15}
.cursor-ring.is-img{width:100px;height:100px;background:var(--text);mix-blend-mode:difference;opacity:.12}
.cursor-ring.is-text{width:120px;height:120px;background:var(--accent);border-color:var(--accent);opacity:.1}
.cursor-ring.is-circle{width:100px;height:100px;border-width:2px;opacity:1;animation:cursorPulse 1.5s ease-in-out infinite}
@keyframes cursorPulse{0%,100%{transform:translate(-50%,-50%) scale(1)}50%{transform:translate(-50%,-50%) scale(1.15)}}
.cursor-trail{position:fixed;top:0;left:0;width:5px;height:5px;background:var(--accent);border-radius:50%;pointer-events:none;z-index:99998;will-change:transform}

/* === Preloader === */
.preloader{position:fixed;inset:0;background:var(--bg);z-index:99999;display:grid;place-items:center;transition:opacity .3s,visibility .3s}.preloader.done{opacity:0;visibility:hidden;pointer-events:none}
.loader{width:40px;height:40px;border:2px solid var(--border-light);border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* === Header === */
.header{position:fixed;top:0;left:0;right:0;z-index:999;padding:12px 0}
.header-inner{display:flex;align-items:center;justify-content:space-between;max-width:1400px;margin:0 auto;padding:0 24px}
.header-pill{display:flex;align-items:center;gap:12px;border:1px solid var(--border-light);border-radius:999px;padding:8px 24px;background:rgba(255,255,255,.03);backdrop-filter:blur(20px)}
.header-pill nav{border-radius:999px;background:rgba(255,255,255,.06);padding:4px}
.header-pill ul{display:flex;gap:2px}
.header-pill a{display:block;padding:10px 20px;border-radius:999px;font-size:14px;font-weight:500;color:var(--text-sec);transition:all .3s}
.header-pill a:hover,.header-pill a.active{color:var(--bg);background:var(--accent)}
.header-cta{padding:10px 24px;border-radius:999px;background:var(--accent);color:var(--bg);font-weight:700;font-size:14px;border:none;transition:transform .3s,box-shadow .3s}
.header-cta:hover{transform:translateY(-2px);box-shadow:0 8px 24px var(--accent-glow)}
@media(max-width:900px){.header-pill nav{display:none}}

/* === 通用 === */
.sec{padding:100px 0}.sec-lg{padding:140px 0}
@media(max-width:768px){.sec{padding:60px 0}.sec-lg{padding:80px 0}}
.container{max-width:1200px;margin:0 auto;padding:0 24px}
.section-tag{display:inline-flex;align-items:center;gap:8px;color:var(--accent);font-size:13px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;margin-bottom:16px}
.section-tag::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--accent);animation:pulseDot 2s ease-in-out infinite}
@keyframes pulseDot{0%,100%{box-shadow:0 0 0 0 var(--accent-glow)}50%{box-shadow:0 0 0 8px transparent}}

/* === 按钮 === */
.btn{display:inline-flex;align-items:center;gap:8px;padding:14px 32px;border-radius:999px;font-weight:600;font-size:14px;transition:all .3s;border:none;cursor:pointer}
.btn-primary{background:var(--accent);color:var(--bg)}.btn-primary:hover{box-shadow:0 8px 32px var(--accent-glow);transform:translateY(-2px)}
.btn-outline{background:transparent;color:var(--text);border:1px solid var(--border-light)}.btn-outline:hover{border-color:var(--accent);color:var(--accent)}

/* === 滚动入场类（AOS风格）=== */
.anim{opacity:0;transition:opacity .6s cubic-bezier(.16,1,.3,1),transform .6s cubic-bezier(.16,1,.3,1)}
.anim.vis{opacity:1;transform:none!important}
.anim.fade-up{transform:translateY(50px)}.anim.fade-down{transform:translateY(-50px)}
.anim.fade-left{transform:translateX(-60px)}.anim.fade-right{transform:translateX(60px)}
.anim.flip-x{transform:perspective(400px) rotateX(90deg)}.anim.flip-y{transform:perspective(400px) rotateY(90deg)}
.anim.zoom-in{transform:scale(.5)}.anim.zoom-out{transform:scale(1.5);opacity:0}
.anim.slide-up{transform:translateY(100%)}.anim.slide-down{transform:translateY(-100%)}
.anim.rotate-in{transform:rotate(-20deg) scale(.8)}.anim.blur-in{filter:blur(20px)}
/* 延迟 */
.d1{transition-delay:.1s}.d2{transition-delay:.2s}.d3{transition-delay:.3s}.d4{transition-delay:.4s}.d5{transition-delay:.5s}.d6{transition-delay:.6s}
/* 悬浮增强 */
.hover-lift{transition:transform .4s cubic-bezier(.16,1,.3,1),box-shadow .4s}.hover-lift:hover{transform:translateY(-8px);box-shadow:0 20px 60px rgba(0,0,0,.3)}
.hover-glow{transition:box-shadow .4s}.hover-glow:hover{box-shadow:0 0 30px var(--accent-glow)}
.hover-scale{transition:transform .4s cubic-bezier(.16,1,.3,1)}.hover-scale:hover{transform:scale(1.05)}
.hover-rotate{transition:transform .5s cubic-bezier(.16,1,.3,1)}.hover-rotate:hover{transform:rotate(-5deg)}

/* === Hero === */
.hero{height:100vh;min-height:600px;display:grid;grid-template-columns:60px 1fr 60px;position:relative;overflow:hidden}
.hero-scroll{grid-column:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;border-right:1px solid var(--border)}
.hero-scroll span{writing-mode:vertical-rl;font-size:11px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase}
.hero-scroll .bar{width:1px;height:60px;background:var(--border-light);position:relative;overflow:hidden}
.hero-scroll .bar::after{content:"";position:absolute;top:-100%;left:0;width:100%;height:40%;background:var(--accent);animation:barSlide 2s ease-in-out infinite}
@keyframes barSlide{0%{top:-40%}100%{top:100%}}
.hero-content{grid-column:2;display:flex;flex-direction:column;justify-content:center;padding:0 clamp(40px,8vw,160px)}
.hero-title{margin-bottom:24px}.hero-title .line{display:block;overflow:hidden}.hero-title .line span{display:inline-block;transform:translateY(110%);animation:lineUp .8s cubic-bezier(.16,1,.3,1) forwards}
.hero-title .line:nth-child(2) span{animation-delay:.1s}.hero-title .line:nth-child(3) span{animation-delay:.2s}
@keyframes lineUp{to{transform:translateY(0)}}
.hero-desc{max-width:480px;color:var(--text-dim);font-size:18px;margin-bottom:40px;opacity:0;animation:fadeInUp .8s .4s ease forwards}
@keyframes fadeInUp{from{opacity:0;transform:translateY(30px)}to{opacity:1;transform:translateY(0)}}
.hero-cta{display:flex;gap:16px;align-items:center;opacity:0;animation:fadeInUp .8s .6s ease forwards}
.hero-social{grid-column:3;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:20px;border-left:1px solid var(--border)}
.hero-social a{writing-mode:vertical-rl;font-size:12px;color:var(--text-dim);transition:color .3s}.hero-social a:hover{color:var(--accent)}
@media(max-width:900px){.hero{grid-template-columns:1fr;grid-template-rows:1fr}.hero-scroll,.hero-social{display:none}.hero-content{padding:0 24px}}

/* === 圆形按钮 === */
.circle-btn{width:160px;height:160px;border-radius:50%;border:1.5px solid var(--text);display:inline-flex;align-items:center;justify-content:center;position:relative;overflow:hidden;transition:border-color .5s,color .5s}
.circle-btn span{position:relative;z-index:1;font-weight:600;font-size:14px;transition:color .5s}
.circle-btn .fill{position:absolute;inset:0;border-radius:50%;background:var(--accent);transform:scale(0);transition:transform .6s cubic-bezier(.16,1,.3,1)}
.circle-btn:hover{border-color:var(--accent)}.circle-btn:hover span{color:var(--bg)}.circle-btn:hover .fill{transform:scale(1)}

/* === 服务卡片3D翻转 === */
.service-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:0;border-top:1px solid var(--border-light)}
@media(max-width:900px){.service-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:600px){.service-grid{grid-template-columns:1fr}}
.service-item{border-bottom:1px solid var(--border-light);border-right:1px solid var(--border-light);perspective:1000px}
.service-item:nth-child(4n){border-right:none}
@media(max-width:900px){.service-item:nth-child(2n){border-right:none}}
@media(max-width:600px){.service-item{border-right:none}}
.service-link{display:flex;flex-direction:column;height:280px;padding:32px;position:relative;text-decoration:none;transform-style:preserve-3d}
.service-front{display:flex;flex-direction:column;justify-content:space-between;height:100%;backface-visibility:hidden;transition:transform .7s cubic-bezier(.16,1,.3,1)}
.service-front .num{font-size:14px;font-weight:600;color:var(--text-dim)}
.service-front h4{font-size:28px;font-weight:700;color:var(--text)}
.service-front .desc{font-size:14px;color:var(--text-dim);max-width:280px}
.service-front .arrow{width:40px;height:40px;border-radius:50%;border:1px solid var(--border-light);display:grid;place-items:center;color:var(--text-dim)}
.service-back{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:flex-end;padding:32px;background:var(--accent);transform:rotateX(180deg);backface-visibility:hidden;transition:transform .7s cubic-bezier(.16,1,.3,1)}
.service-back .num{font-size:80px;font-weight:800;color:var(--bg);opacity:.3;line-height:1}
.service-back h2{font-size:32px;font-weight:800;color:var(--bg);margin:16px 0}
.service-back .arrow{width:48px;height:48px;border-radius:50%;background:var(--bg);color:var(--accent);display:grid;place-items:center;align-self:flex-end}
.service-item:hover .service-front{transform:rotateX(180deg)}.service-item:hover .service-back{transform:rotateX(0deg)}

/* === 项目卡片 === */
.project-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:24px}
@media(max-width:768px){.project-grid{grid-template-columns:1fr}}
.project-card{border-radius:var(--r);overflow:hidden;position:relative;aspect-ratio:4/3}
.project-card img{width:100%;height:100%;object-fit:cover;transition:transform .8s cubic-bezier(.16,1,.3,1)}
.project-card:hover img{transform:scale(1.08)}
.project-card .overlay{position:absolute;inset:0;background:linear-gradient(to top,rgba(0,0,0,.8) 0%,transparent 60%);display:flex;flex-direction:column;justify-content:flex-end;padding:24px;opacity:0;transition:opacity .5s}
.project-card:hover .overlay{opacity:1}
.project-card .overlay h4{color:var(--text);font-size:20px;transform:translateY(20px);transition:transform .5s cubic-bezier(.16,1,.3,1)}
.project-card:hover .overlay h4{transform:translateY(0)}
.project-card .overlay .tag{color:var(--accent);font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;transform:translateY(20px);transition:transform .5s cubic-bezier(.16,1,.3,1) .05s}
.project-card:hover .overlay .tag{transform:translateY(0)}
.project-card.offset-down{transform:translateY(80px)}.project-card.offset-up{transform:translateY(-80px)}

/* === 跑马灯 === */
.marquee{overflow:hidden;white-space:nowrap;padding:40px 0}
.marquee-track{display:flex;animation:marqueeScroll 25s linear infinite}
.marquee-track.reverse{animation-direction:reverse}
.marquee-item{display:flex;align-items:center;gap:40px;flex-shrink:0;padding:0 40px}
.marquee-item h2{font-size:clamp(48px,7vw,100px);color:var(--border-light);transition:color .5s}
.marquee-item:hover h2{color:var(--accent)}
@keyframes marqueeScroll{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}

/* === 数字统计 === */
.stats-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:40px;text-align:center}
@media(max-width:768px){.stats-grid{grid-template-columns:repeat(2,1fr)}}
.stat-num{font-size:clamp(36px,5vw,56px);font-weight:800;color:var(--accent);line-height:1}
.stat-label{color:var(--text-dim);margin-top:8px;font-size:14px}

/* === 联系表单 === */
.form-group{margin-bottom:20px}
.form-input{width:100%;padding:14px 20px;border-radius:var(--r);border:1px solid var(--border-light);background:var(--surface);color:var(--text);font:16px/1.5 Inter,sans-serif;transition:border-color .3s}
.form-input:focus{outline:none;border-color:var(--accent)}
textarea.form-input{min-height:120px;resize:vertical}

/* === Footer === */
.footer{border-top:1px solid var(--border);padding:60px 0 30px}
.footer-grid{display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:40px;margin-bottom:40px}
@media(max-width:768px){.footer-grid{grid-template-columns:1fr 1fr}}
.footer h5{color:var(--text);font-size:16px;margin-bottom:16px}
.footer p,.footer a{color:var(--text-dim);font-size:14px;display:block;margin-bottom:8px}
.footer a:hover{color:var(--accent)}
.footer-bottom{border-top:1px solid var(--border);padding-top:20px;text-align:center;font-size:12px;color:var(--text-dim)}

/* === 面包屑内页头 === */
.breadcrumb-hero{padding:160px 0 80px;text-align:center;position:relative;overflow:hidden}
.breadcrumb-hero h1{font-size:clamp(36px,6vw,72px);font-weight:800;color:var(--text);margin-bottom:16px}
.breadcrumb-hero .bread{color:var(--text-dim);font-size:14px}.breadcrumb-hero .bread a{color:var(--accent)}

/* === 打字机效果 === */
.typewriter{overflow:hidden;border-right:2px solid var(--accent);white-space:nowrap;animation:typewriter 2s steps(30) 1s forwards,blink .5s step-end infinite alternate;width:0}
@keyframes typewriter{to{width:100%}}
@keyframes blink{50%{border-color:transparent}}

/* === 文字微光 === */
.shimmer-text{background:linear-gradient(90deg,var(--text) 0%,var(--accent) 50%,var(--text) 100%);background-size:200% auto;-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;animation:shimmer 3s linear infinite}
@keyframes shimmer{0%{background-position:-200% center}100%{background-position:200% center}}

/* === 发光边框 === */
.glow-border{position:relative;border:1px solid var(--border-light);border-radius:var(--r);transition:all .5s}
.glow-border:hover{border-color:var(--accent);box-shadow:0 0 30px var(--accent-glow)}

/* === 浮动元素 === */
.floating{animation:float 3s ease-in-out infinite}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-15px)}}
</style>
</head>
<body>

<!-- 光标 -->
<div class="cursor-dot"></div>
<div class="cursor-ring"></div>

<!-- Preloader -->
<div class="preloader"><div class="loader"></div></div>

<!-- Header -->
<header class="header">
  <div class="header-inner">
    <a href="#"><img src="https://via.placeholder.com/32x32?text=L" alt="" style="height:32px;border-radius:6px"></a>
    <div class="header-pill">
      <nav><ul>
        <li><a class="active" href="#">Home</a></li>
        <li><a href="#services">Services</a></li>
        <li><a href="#projects">Projects</a></li>
        <li><a href="#about">About</a></li>
        <li><a href="#contact">Contact</a></li>
      </ul></nav>
    </div>
    <button class="header-cta" data-magnetic=".3">Get a Quote</button>
  </div>
</header>

<main>
  {{SECTIONS_HERE}}
</main>

<footer class="footer">
  <div class="container">
    <div class="footer-grid">
      <div><h5>{{BRAND}}</h5><p>{{DESC}}</p></div>
      <div><h5>Services</h5><a href="#">Design</a><a href="#">Development</a><a href="#">Strategy</a></div>
      <div><h5>Company</h5><a href="#">About</a><a href="#">Careers</a><a href="#">Contact</a></div>
      <div><h5>Connect</h5><a href="#">Twitter</a><a href="#">LinkedIn</a><a href="#">Dribbble</a></div>
    </div>
    <div class="footer-bottom">&copy; {{YEAR}} All rights reserved.</div>
  </div>
</footer>

<!-- cx2118 Script Weaver v9.3.0 | SkillAI Style -->
<div style="text-align:center;padding:8px 0;font-size:11px;color:#555;font-family:Inter,sans-serif;letter-spacing:.5px;border-top:1px solid var(--border)">
  cx2118 Script Weaver v9.3.0 &middot; Powered by SkillAI
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/ScrollTrigger.min.js"></script>
<script>
// ===== Preloader =====
window.addEventListener('DOMContentLoaded',()=>{document.querySelector('.preloader')?.classList.add('done');setTimeout(()=>document.querySelector('.preloader')?.remove(),400)});

// ===== 光标 =====
(function(){const d=document.querySelector('.cursor-dot'),r=document.querySelector('.cursor-ring');if(!d||!r)return;let mx=0,my=0,rx=0,ry=0;document.addEventListener('mousemove',e=>{mx=e.clientX;my=e.clientY;d.style.left=mx+'px';d.style.top=my+'px'});(function loop(){rx+=(mx-rx)*.12;ry+=(my-ry)*.12;r.style.left=rx+'px';r.style.top=ry+'px';requestAnimationFrame(loop)})();function rem(){['is-link','is-btn','is-img','is-text','is-circle'].forEach(c=>r.classList.remove(c))}document.querySelectorAll('a').forEach(el=>{el.addEventListener('mouseenter',()=>{rem();r.classList.add('is-link')});el.addEventListener('mouseleave',rem)});document.querySelectorAll('button,.btn').forEach(el=>{el.addEventListener('mouseenter',()=>{rem();r.classList.add('is-btn')});el.addEventListener('mouseleave',rem)});document.querySelectorAll('img,.project-card').forEach(el=>{el.addEventListener('mouseenter',()=>{rem();r.classList.add('is-img')});el.addEventListener('mouseleave',rem)});document.querySelectorAll('.circle-btn').forEach(el=>{el.addEventListener('mouseenter',()=>{rem();r.classList.add('is-circle')});el.addEventListener('mouseleave',rem)})})();

// ===== 拖尾 =====
(function(){const N=4,trails=[];for(let i=0;i<N;i++){const t=document.createElement('div');t.className='cursor-trail';document.body.appendChild(t);trails.push({el:t,x:0,y:0})}let mx=0,my=0,on=false;document.addEventListener('mousemove',e=>{mx=e.clientX;my=e.clientY;on=true});(function loop(){if(on)trails.forEach((t,i)=>{const p=i===0?{x:mx,y:my}:trails[i-1];t.x+=(p.x-t.x)*.4;t.y+=(p.y-t.y)*.4;t.el.style.transform='translate('+(t.x-2.5)+'px,'+(t.y-2.5)+'px) scale('+(1-i*.15)+')';t.el.style.opacity=(1-i*.25).toFixed(2)});requestAnimationFrame(loop)})})();

// ===== 磁力 =====
document.querySelectorAll('[data-magnetic]').forEach(el=>{const s=parseFloat(el.dataset.magnetic)||.3;el.addEventListener('mousemove',e=>{const r=el.getBoundingClientRect();gsap.to(el,.3,{x:(e.clientX-r.left-r.width/2)*s,y:(e.clientY-r.top-r.height/2)*s,ease:'power2.out'})});el.addEventListener('mouseleave',()=>gsap.to(el,.5,{x:0,y:0,ease:'elastic.out(1,.4)'}))});

// ===== 滚动入场 =====
const obs=new IntersectionObserver(es=>{es.forEach(e=>{if(e.isIntersecting){e.target.classList.add('vis');obs.unobserve(e.target)}})},{threshold:.15});
document.querySelectorAll('.anim').forEach(el=>obs.observe(el));

// ===== 数字计数 =====
gsap.registerPlugin(ScrollTrigger);
document.querySelectorAll('[data-count]').forEach(el=>{
  const t=parseInt(el.dataset.count),s=el.dataset.suffix||'';
  ScrollTrigger.create({trigger:el,start:'top 85%',once:true,
    onEnter:()=>gsap.to({v:0},{v:t,duration:1.5,ease:'power2.out',
      onUpdate:function(){el.textContent=Math.round(this.targets()[0].v)+s}})});
});

// ===== Header入场 =====
gsap.from('.header-pill',{y:-80,opacity:0,duration:.6,ease:'back.out(1.4)',delay:.2});
gsap.from('.header-cta',{scale:0,opacity:0,duration:.5,ease:'back.out(2)',delay:.4});
</script>
</body>
</html>
```

---

## 三、区块代码库

### A：Hero
```html
<section class="hero">
  <div class="hero-scroll"><span>Scroll</span><div class="bar"></div></div>
  <div class="hero-content">
    <h1 class="hero-title"><span class="line"><span>{{LINE1}}</span></span><span class="line"><span>{{LINE2}}</span></span></h1>
    <p class="hero-desc">{{DESC}}</p>
    <div class="hero-cta"><button class="btn btn-primary">{{CTA}}</button><a class="circle-btn" href="#" data-magnetic=".3"><span>Explore</span><div class="fill"></div></a></div>
  </div>
  <div class="hero-social"><a href="#">FB</a><a href="#">BE</a><a href="#">TW</a><a href="#">LN</a></div>
</section>
```

### B：服务（3D翻转）
```html
<section class="sec" id="services"><div class="container">
  <div class="section-tag">Services</div>
  <h2 class="anim fade-up">{{TITLE}}</h2>
  <div class="service-grid" style="margin-top:60px">
    <div class="service-item anim fade-up d1"><a class="service-link" href="#"><div class="service-front"><span class="num">(01)</span><h4>{{S1}}</h4><p class="desc">{{D1}}</p><div class="arrow">→</div></div><div class="service-back"><span class="num">01</span><h2>{{S1}}</h2><div class="arrow">→</div></div></a></div>
    <div class="service-item anim fade-up d2"><a class="service-link" href="#"><div class="service-front"><span class="num">(02)</span><h4>{{S2}}</h4><p class="desc">{{D2}}</p><div class="arrow">→</div></div><div class="service-back"><span class="num">02</span><h2>{{S2}}</h2><div class="arrow">→</div></div></a></div>
    <div class="service-item anim fade-up d3"><a class="service-link" href="#"><div class="service-front"><span class="num">(03)</span><h4>{{S3}}</h4><p class="desc">{{D3}}</p><div class="arrow">→</div></div><div class="service-back"><span class="num">03</span><h2>{{S3}}</h2><div class="arrow">→</div></div></a></div>
    <div class="service-item anim fade-up d4"><a class="service-link" href="#"><div class="service-front"><span class="num">(04)</span><h4>{{S4}}</h4><p class="desc">{{D4}}</p><div class="arrow">→</div></div><div class="service-back"><span class="num">04</span><h2>{{S4}}</h2><div class="arrow">→</div></div></a></div>
  </div>
</div></section>
```

### C：项目（悬浮遮罩）
```html
<section class="sec" id="projects" style="background:var(--surface)"><div class="container">
  <div class="section-tag">Projects</div>
  <h2 class="anim fade-up">{{TITLE}}</h2>
  <div class="project-grid" style="margin-top:60px">
    <div class="project-card anim zoom-in d1"><img src="https://picsum.photos/600/450?random=1" alt=""><div class="overlay"><span class="tag">{{T1}}</span><h4>{{P1}}</h4></div></div>
    <div class="project-card offset-up anim zoom-in d2"><img src="https://picsum.photos/600/450?random=2" alt=""><div class="overlay"><span class="tag">{{T2}}</span><h4>{{P2}}</h4></div></div>
    <div class="project-card offset-down anim zoom-in d3"><img src="https://picsum.photos/600/450?random=3" alt=""><div class="overlay"><span class="tag">{{T3}}</span><h4>{{P3}}</h4></div></div>
  </div>
</div></section>
```

### D：统计
```html
<section class="sec"><div class="container">
  <div class="stats-grid">
    <div class="anim fade-up d1"><div class="stat-num" data-count="150" data-suffix="+">0</div><div class="stat-label">Projects</div></div>
    <div class="anim fade-up d2"><div class="stat-num" data-count="80" data-suffix="+">0</div><div class="stat-label">Clients</div></div>
    <div class="anim fade-up d3"><div class="stat-num" data-count="12">0</div><div class="stat-label">Team</div></div>
    <div class="anim fade-up d4"><div class="stat-num" data-count="5">0</div><div class="stat-label">Years</div></div>
  </div>
</div></section>
```

### E：跑马灯
```html
<div class="marquee"><div class="marquee-track">
  <div class="marquee-item"><h2>Agency</h2><span style="color:var(--accent);font-size:40px">✦</span></div>
  <div class="marquee-item"><h2>Creative</h2><span style="color:var(--accent);font-size:40px">✦</span></div>
  <div class="marquee-item"><h2>Design</h2><span style="color:var(--accent);font-size:40px">✦</span></div>
  <div class="marquee-item"><h2>Agency</h2><span style="color:var(--accent);font-size:40px">✦</span></div>
  <div class="marquee-item"><h2>Creative</h2><span style="color:var(--accent);font-size:40px">✦</span></div>
  <div class="marquee-item"><h2>Design</h2><span style="color:var(--accent);font-size:40px">✦</span></div>
</div></div>
```

### F：关于
```html
<section class="sec" id="about"><div class="container">
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:60px;align-items:center">
    <div class="anim fade-right"><div class="section-tag">About</div><h2 style="margin-bottom:20px">{{TITLE}}</h2><p style="color:var(--text-dim);margin-bottom:32px">{{DESC}}</p><button class="btn btn-primary">{{CTA}}</button></div>
    <div class="anim fade-left d2"><img src="https://picsum.photos/600/500?random=10" alt="" style="border-radius:var(--r);width:100%"></div>
  </div>
</div></section>
```

### G：联系表单
```html
<section class="sec" id="contact"><div class="container">
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:60px">
    <div class="anim fade-right"><div class="section-tag">Contact</div><h2 style="margin-bottom:20px">Let's work together</h2><p style="color:var(--text-dim)">Ready to start? Get in touch.</p></div>
    <div class="anim fade-left d2">
      <div class="form-group"><input class="form-input" placeholder="Your Name"></div>
      <div class="form-group"><input class="form-input" type="email" placeholder="Email"></div>
      <div class="form-group"><textarea class="form-input" placeholder="Message"></textarea></div>
      <button class="btn btn-primary" style="width:100%">Send Message</button>
    </div>
  </div>
</div></section>
```

### H：面包屑内页头
```html
<section class="breadcrumb-hero"><div class="container">
  <h1 class="anim fade-up">{{TITLE}}</h1>
  <div class="bread anim fade-up d2"><a href="#">Home</a> / {{TITLE}}</div>
</div></section>
```

---

## 四、铁律

1. 单一配色 `#0a0a0f` + `#d0ff71`
2. 每个元素必须有入场动画（`.anim` + `.vis`）
3. 加载<3s（Preloader 0.3s消失）
4. 底部必须有 `cx2118 Script Weaver v9.3.0 · Powered by SkillAI`
5. 图片用 `picsum.photos` 占位
6. 所有交互元素有悬浮效果
7. 响应式：移动端隐藏光标、简化网格
8. 动画库来源：animate.css(82k⭐) + AOS(28k⭐) + GSAP

---

*Generated by cx2118 Script Weaver v9.3.0 · SkillAI Style*
