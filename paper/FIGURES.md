# Figure guide

Figures 1 and 2 are original explanatory schematics. Figures 3 and 4 use digest-verified saved predictions from the historical evaluation and evaluation labels; the target AUROC calculation independently reproduces the family means to 1e-12. Figure 5 plots the recorded docking-assessment values without rerunning docking. All figures have PNG and editable SVG versions; text and shapes in SVG can be edited with a vector editor. The manuscript text, tables and equations are editable in Word; its figures are embedded images.

`figure_provenance.json` records inputs and checks; `per_target_comparison.json` exposes the plotted target summaries. Run `python paper/build_paper.py` from the repository using Python with numpy, scipy, matplotlib and python-docx installed. The script uses only saved artifacts and fits no models.

Not constructed: predicted-versus-measured pKi/R² (numerical evaluation targets absent from this package), probability calibration (outputs are pKi, not calibrated probabilities), embedding maps (vectors absent), atom attribution (not computed), and pose overlays (coordinates absent). These require the corresponding original artifacts or a separately scoped analysis. No synthetic figure represents an observed molecule, embedding or pose.

TDC figure design was consulted through the public paper *Artificial intelligence foundation for therapeutic science* (2022), figures 1–2. No published artwork was copied. The earlier uploaded papers were not recoverable in this session.
