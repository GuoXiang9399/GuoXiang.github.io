---
layout: splash
permalink: /
redirect_from:
  - /about/
  - /about.html
title: "HENU GUO Lab"
excerpt: "河南大学基础医学院病原生物学系<br/>Department of Pathogen Biology, School of Basic Medical Sciences, Henan University<br/>河南大学深圳研究院病原与媒介生物学创新研究中心<br/>Research Center for Innovation in Pathogen & Vector Biology, Shenzhen Research Institute, Henan University"
header:
  overlay_image: campus/minglun-cover.jpg
  overlay_filter: 0.45
  caption: ""
  cta_url: /team/
  cta_label: "认识我们 Meet the Team"
---

<script>
(function () {
  var hero = document.querySelector('.page__hero--overlay');
  if (!hero) return;
  var m = (hero.style.backgroundImage || '').match(/url\(['"]?([^'")]+)['"]?\)/);
  var dir = m ? m[1].replace(/[^\/]+$/, '') : '/images/campus/';
  var filter = 'linear-gradient(rgba(0, 0, 0, 0.45), rgba(0, 0, 0, 0.45))';
  var files = [
    'minglun-cover.jpg',
    'minglun-2.jpg',
    'minglun-3.jpg',
    'jinming-cover.jpg',
    'jinming-2.jpg',
    'jinming-3.jpg'
  ];
  var slides = [];
  files.forEach(function (f, i) {
    var d = document.createElement('div');
    d.className = 'hero-slide' + (i === 0 ? ' is-active' : '');
    d.style.backgroundImage = filter + ', url("' + dir + f + '")';
    hero.insertBefore(d, hero.firstChild);
    slides.push(d);
  });
  var cur = 0;
  setInterval(function () {
    slides[cur].classList.remove('is-active');
    cur = (cur + 1) % slides.length;
    slides[cur].classList.add('is-active');
  }, 6000);
})();
</script>


