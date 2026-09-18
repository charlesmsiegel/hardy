# Keller Threefold Claimed Proof Ledger

**Snapshot:** 2026-09-14

This is the proof layer of the temporary mini-Hardy state. Each active claim records its direct claim dependencies, the current manuscript statement, and the manuscript's claimed proof/derivation.

This file does **not** promote anything to human-verified or Lean-verified status.

## K05-01 — Rigidity of extended Rees algebras

**Status:** llm proved

**Claim.** For a smooth affine complex surface Σ with log Kodaira dimension κ̄(Σ) ≥ 0, every finitely generated extended Rees algebra of a nonzero multiplicative ideal filtration is rigid (equivalently its Makar-Limanov invariant is the whole ring).

**Direct dependencies:** none

**Manuscript label:** `thm:rees-rigidity`

### Current manuscript statement

```tex
\begin{theorem}[Rigidity of extended Rees algebras]\label{thm:rees-rigidity}
Let $\Sigma=\Spec R$ be a smooth affine complex surface with $\kbar(\Sigma)\geq0$.  Let $\cE$ be a finitely generated extended Rees algebra as in \eqref{eq:extended-rees}.  Then $\cE$ admits no nonzero locally nilpotent derivation.  Equivalently,
\[
  \ML(\cE)=\cE.
\]
\end{theorem}
```

### Claimed proof

```tex
\begin{proof}[Conceptual proof via graded cylindricity]
If $\cE$ were nonrigid, \cref{prop:rees-cylinder} would give $\kbar(\Sigma)=-\infty$, contrary to the hypothesis.  Hence $\cE$ is rigid.
\end{proof}
```

---

## K06-01 — Explicit Keller map is étale and noninjective

**Status:** llm proved

**Claim.** The displayed polynomial map F : A^3 → A^3 satisfies det JF = -2 and has a fiber containing three explicit distinct points; hence it is étale and noninjective.

**Direct dependencies:** none

**Manuscript label:** `prop:keller`

### Current manuscript statement

```tex
\begin{proposition}\label{prop:keller}
The map $F$ satisfies
\[
  \det JF=-2.
\]
Moreover,
\[
  F(0,0,-1/4)
  =F(1,-3/2,13/2)
  =F(-1,3/2,13/2)
  =(-1/4,0,0).
\]
Hence $F$ is an etale, noninjective polynomial self-map of $\A^3$.
\end{proposition}
```

### Claimed proof

```tex
\begin{proof}
The change $(x,y,z)\mapsto(a,y,r)$ on $\alpha\neq0$ has Jacobian determinant $-\alpha$, while \eqref{eq:transformed-F} gives
\[
  \det\frac{\partial(a,b,c)}{\partial(a,y,r)}
  =2(1-yr)=\frac{2}{\alpha}.
\]
Their product is $-2$.  Since both sides are polynomials, the identity extends across $\alpha=0$.  The three displayed substitutions are immediate.
\end{proof}
```

---

## K06-02 — Simple-root incidence model

**Status:** llm proved

**Claim.** The source A^3 is isomorphic to the simple-projective-root locus of the binary cubic Φ_{A,B,C}(S,T)=2AS^3-BS^2T+2ST^2-CT^3; fibers of F correspond to simple projective roots.

**Direct dependencies:** `K06-01`

**Manuscript label:** `thm:incidence-isomorphism`

### Current manuscript statement

```tex
\begin{theorem}[Simple-root incidence model]\label{thm:incidence-isomorphism}
The morphism
\begin{equation}\label{eq:iota}
  \iota\colon\A^3\longrightarrow\cI,
  \qquad
  (x,y,z)\longmapsto\bigl(F(x,y,z),[x:1+xy]\bigr),
\end{equation}
induces an isomorphism
\[
  \A^3\xrightarrow{\sim}\cI^{\mathrm{simp}}.
\]
Consequently, the points of $F^{-1}(A,B,C)$ are in natural bijection with the simple projective roots of $\Phi_{A,B,C}$.
\end{theorem}
```

### Claimed proof

```tex
\begin{proof}
The pair $[x:1+xy]$ is defined everywhere because $x$ and $1+xy$ cannot vanish simultaneously.  Homogenizing \eqref{eq:transformed-F} gives the polynomial identity
\begin{equation}\label{eq:binary-identity}
  2ax^3-bx^2(1+xy)+2x(1+xy)^2-c(1+xy)^3=0,
\end{equation}
so \eqref{eq:iota} lands in $\cI$.

On the chart $T\neq0$, put $r=S/T$.  The equation is
\[
  \phi(r)=2Ar^3-Br^2+2r-C=0.
\]
Set
\begin{equation}\label{eq:T-chart-inverse}
  D_r=1-Br+3Ar^2=\frac12\phi'(r),
  \qquad
  y=B-3Ar,
  \qquad
  x=\frac{r}{D_r},
\end{equation}
\begin{equation}\label{eq:T-chart-z}
  z=AD_r^3-y^2(4-ry)D_r.
\end{equation}
The root is simple exactly when $D_r\neq0$, so these are regular functions on the simple-root locus of this chart.  They satisfy
\[
  1+xy=D_r^{-1},
  \qquad
  \frac{x}{1+xy}=r,
\]
and direct substitution into \eqref{eq:F}, or equivalently into \eqref{eq:transformed-F}, gives $(a,b,c)=(A,B,C)$.

On the chart $S\neq0$, put $u=T/S$.  The equation is
\[
  \psi(u)=2A-Bu+2u^2-Cu^3=0.
\]
Set
\begin{equation}\label{eq:S-chart-inverse}
  y=-\frac B2+3u-\frac32Cu^2,
  \qquad
  E=u-y=\frac B2-2u+\frac32Cu^2=-\frac12\psi'(u),
\end{equation}
\begin{equation}\label{eq:S-chart-xz}
  x=E^{-1},
  \qquad
  z=2E^2-3yE-CE^3.
\end{equation}
Again, simplicity is equivalent to $E\neq0$.  These formulas give
\[
  \frac{1+xy}{x}=u
\]
and recover the target coordinates.  On the overlap $u=r^{-1}$, the two sets of formulas agree.  At $u=0$, the incidence equation forces $A=0$, and the formulas reduce to
\[
  x=\frac2B,
  \qquad
  y=-\frac B2,
  \qquad
  z=\frac{5x-C}{x^3},
\]
which are regular because simplicity at infinity is equivalent to $B\neq0$.

For a source point in the $T$-chart, \eqref{eq:transformed-F} gives
\[
  D_r=1-yr=(1+xy)^{-1},
\]
so its root is simple.  On the $S$-chart one has $E=x^{-1}$.  Thus the displayed formulas are inverse to \eqref{eq:iota} on both charts.
\end{proof}
```

---

## K06-03 — Finite incidence normalization

**Status:** llm proved

**Claim.** The incidence variety is smooth and irreducible; its projection to target A^3 is finite of degree three and its étale locus is exactly the simple-root locus.

**Direct dependencies:** `K06-02`

**Manuscript label:** `prop:incidence-finite`

### Current manuscript statement

```tex
\begin{proposition}\label{prop:incidence-finite}
The variety $\cI$ is smooth and irreducible, and the projection
\[
  \pi\colon\cI\longrightarrow\A^3_{A,B,C}
\]
is finite of degree three.  Its etale locus is exactly $\cI^{\mathrm{simp}}$.
\end{proposition}
```

### Claimed proof

```tex
\begin{proof}
On the chart $T\neq0$, the equation is linear in $C$, with derivative $-1$; on the chart $S\neq0$, it is linear in $A$, with derivative $2$.  Thus $\cI$ is smooth.  The $T\neq0$ chart is isomorphic to $\A^3_{A,B,r}$ and is dense: the locus $T=0$ in $\cI$ is given by $A=0$ and has dimension two.  Hence $\cI$ is irreducible.

The projection is projective, hence proper, because $\cI$ is closed in $\A^3\times\mathbb P^1$ \cite[Chapter II, Section 4]{Hartshorne1977}; see also \cite[Tag 01W7]{StacksProject}.  Every fiber is finite, since the coefficient of $ST^2$ in \eqref{eq:binary-cubic} is the constant $2$, so the binary cubic is never the zero polynomial.  A proper quasi-finite morphism is finite \cite[Tag 0F2P]{StacksProject}.  The generic fiber has three points.  On either affine chart the relative equation is a one-variable cubic, and the relative Jacobian criterion \cite[Tag 01V4, Lemma 29.35.14]{StacksProject} says that the projection is etale exactly where the derivative with respect to the root parameter is nonzero, namely where the chosen root is simple.
\end{proof}
```

---

## K07-01 — Discriminant geometry

**Status:** llm proved

**Claim.** Δ=B^2-16A-B^3C+18ABC-27A^2C^2 is irreducible; its reduced singular locus Γ≅C* is the triple-root locus; the discriminant hypersurface is smooth off Γ and has transverse cusp singularities along Γ.

**Direct dependencies:** `K06-02`

**Manuscript label:** `prop:discriminant-geometry`

### Current manuscript statement

```tex
\begin{proposition}\label{prop:discriminant-geometry}
The polynomial $\Delta$ is irreducible.  Its reduced singular locus is the smooth curve
\begin{equation}\label{eq:Gamma}
  \Gamma
  =V(12A-B^2,\,3BC-4)
  =\left\{\left(\frac1{3t^2},\frac2t,\frac{2t}{3}\right):t\in\C^*\right\}
  \simeq\C^*.
\end{equation}
The points of $\Gamma$ are exactly the target cubics with a triple root.  The hypersurface $\cS$ is smooth away from $\Gamma$; transversely to $\Gamma$ it has the ordinary cubic-discriminant cusp.
\end{proposition}
```

### Claimed proof

```tex
\begin{proof}
Viewed as a quadratic polynomial in $A$, the discriminant of $\Delta$ is
\[
  \disc_A(\Delta)=-4(3BC-4)^3,
\]
which is not a square in $\C(B,C)$.  Gauss's lemma for primitive polynomials over a UFD gives irreducibility \cite[Chapter 1]{Eisenbud1995}.

A finite triple root $s=t$ forces
\[
  \Phi(S,T)=2A(S-tT)^3.
\]
Comparing coefficients gives
\[
  A=\frac1{3t^2},
  \qquad B=\frac2t,
  \qquad C=\frac{2t}{3},
\]
which is \eqref{eq:Gamma}.  A triple root at infinity is impossible because the coefficient of $ST^2$ is $2$.

For completeness, differentiate \eqref{eq:Delta}:
\[
\begin{aligned}
  \Delta_A&=-16+18BC-54AC^2,\\
  \Delta_B&=2B-3B^2C+18AC,\\
  \Delta_C&=-B^3+18AB-54A^2C.
\end{aligned}
\]
At a critical point one has $C\neq0$.  Put $q=BC$.  The equation $\Delta_A=0$ gives
\[
  AC^2=\frac{9q-8}{27},
\]
and substituting into $C\Delta_B=0$ yields
\[
  -\frac13(3q-4)^2=0.
\]
Hence $q=4/3$ and $AC^2=4/27$, which is precisely \eqref{eq:Gamma}.  Conversely every point of \eqref{eq:Gamma} is critical.

Finally, after translating the triple root and dividing by the leading coefficient, the standard local normal form for a cubic near a triple root \cite[Chapter 1]{GKZ1994} is analytically equivalent to
\[
  r^3+ur+v.
\]
Its discriminant is $-4u^3-27v^2$.  The remaining coefficient gives a smooth parameter along $\Gamma$, so a transverse slice is a cusp.
\end{proof}
```

---

## K07-02 — Complete fiber census and image

**Status:** llm proved

**Claim.** Fibers of F have cardinality 3 off Δ=0, cardinality 1 on the smooth discriminant stratum, and cardinality 0 on Γ; therefore F(A^3)=A^3\Γ.

**Direct dependencies:** `K06-02`, `K07-01`

**Manuscript label:** `thm:fiber-census`

### Current manuscript statement

```tex
\begin{theorem}[Fiber census and image]\label{thm:fiber-census}
For every $(A,B,C)\in\A^3$,
\[
  \#F^{-1}(A,B,C)=
  \begin{cases}
    3,&\Delta(A,B,C)\neq0,\\
    1,&(A,B,C)\in\cS\setminus\Gamma,\\
    0,&(A,B,C)\in\Gamma.
  \end{cases}
\]
Consequently,
\[
  F(\A^3)=\A^3\setminus\Gamma.
\]
\end{theorem}
```

### Claimed proof

```tex
\begin{proof}
By \cref{thm:incidence-isomorphism}, the fiber points are the simple projective roots of \eqref{eq:binary-cubic}.  A cubic off its discriminant has three simple roots.  A discriminant-zero cubic that is not triple has multiplicity pattern $2+1$, hence exactly one simple root.  A triple-root cubic has no simple root.  The triple-root locus is \eqref{eq:Gamma}.
\end{proof}
```

---

## K07-03 — Nonproperness locus

**Status:** llm proved

**Claim.** The nonproperness/asymptotic-value set of F is exactly the discriminant hypersurface V(Δ), not merely the omitted curve Γ.

**Direct dependencies:** `K06-03`, `K07-01`

**Manuscript label:** `thm:nonproperness`

### Current manuscript statement

```tex
\begin{theorem}[Behavior at infinity]\label{thm:nonproperness}
The nonproperness set of $F$ is exactly
\[
  S_F=\cS=V(\Delta).
\]
Thus the omitted set $\Gamma$ has codimension two, while nonproperness occurs along the whole irreducible quartic hypersurface $\cS$.
\end{theorem}
```

### Claimed proof

```tex
\begin{proof}
Over $\A^3\setminus\cS$, the source is identified by \cref{thm:incidence-isomorphism} with the full inverse image of that open set under the finite morphism $\pi\colon\cI\to\A^3$.  Hence $F$ is finite, and therefore proper, over this open set.  Thus $S_F\subseteq\cS$.

It remains to produce escaping families at every point of $\cS$.  First suppose the multiple root is finite.  It cannot be zero, because the derivative of $2As^3-Bs^2+2s-C$ at $s=0$ is $2$.  Write the multiple root as $t\in\C^*$.  The equations $\phi(t)=\phi'(t)=0$ are equivalent to
\begin{equation}\label{eq:multiple-root-parameters}
  B=3At+t^{-1},
  \qquad
  C=t-At^3.
\end{equation}
For $\eps\neq0$, set
\begin{equation}\label{eq:finite-escape}
  x_{\eps}=\frac{t}{\eps},
  \qquad
  y_{\eps}=\frac{1-\eps}{t},
  \qquad
  z_{\eps}=A\eps^3-y_{\eps}^2(4-ty_{\eps})\eps.
\end{equation}
Then $\|(x_{\eps},y_{\eps},z_{\eps})\|\to\infty$ and direct substitution gives
\[
  F(x_{\eps},y_{\eps},z_{\eps})
  =\left(A,B-\frac{\eps}{t},C+\eps t\right)
  \longrightarrow(A,B,C).
\]

The remaining discriminant points have a multiple root at infinity.  For \eqref{eq:binary-cubic} this occurs exactly when $A=B=0$.  Given $(0,0,C)$, set
\begin{equation}\label{eq:infinity-escape}
  x_{\eps}=\frac2\eps,
  \qquad
  y_{\eps}=-\frac\eps2,
  \qquad
  z_{\eps}=\frac{5x_{\eps}-C}{x_{\eps}^3}.
\end{equation}
Then
\[
  F(x_{\eps},y_{\eps},z_{\eps})=(0,\eps,C)
  \longrightarrow(0,0,C),
\]
while the source escapes.  Hence $\cS\subseteq S_F$.
\end{proof}
```

---

## K07-04 — One-point stratum isomorphism

**Status:** llm proved

**Claim.** For T=F^{-1}(V(Δ)), the restriction F|_T : T → V(Δ)\Γ is an isomorphism; in particular T is smooth and irreducible.

**Direct dependencies:** `K06-01`, `K07-02`

**Manuscript label:** `prop:T-isomorphism`

### Current manuscript statement

```tex
\begin{proposition}\label{prop:T-isomorphism}
The restriction
\[
  F|_{\cT}\colon \cT\longrightarrow\cS\setminus\Gamma
\]
is an isomorphism.  In particular, $\cT$ is smooth and irreducible.
\end{proposition}
```

### Claimed proof

```tex
\begin{proof}
The fiber census shows that the restriction is bijective.  It is etale because $F$ is etale.  An etale morphism that is universally injective is an open immersion \cite[Tag 02LC]{StacksProject}; here every geometric fiber has exactly one reduced point.  Since the map is also surjective, it is an isomorphism.  Alternatively, in the finite incidence model the simple-root component over $\cS\setminus\Gamma$ is a degree-one etale cover of the smooth base and hence isomorphic to it.
\end{proof}
```

---

## K07-05 — Monodromy is S3

**Status:** llm proved

**Claim.** The monodromy group of the three-sheeted cover over A^3\V(Δ) is S3.

**Direct dependencies:** `K07-01`, `K07-04`

**Manuscript label:** `prop:monodromy`

### Current manuscript statement

```tex
\begin{proposition}\label{prop:monodromy}
The monodromy group of the three-sheeted covering
\[
  F^{-1}(\A^3\setminus\cS)\longrightarrow\A^3\setminus\cS
\]
is $S_3$.
\end{proposition}
```

### Claimed proof

```tex
\begin{proof}
The source $F^{-1}(\A^3\setminus\cS)=\A^3\setminus\cT$ is irreducible, so the monodromy action is transitive.  Around a smooth point of the discriminant hypersurface, two roots of the cubic coalesce simply while the third remains separate.  After choosing a transverse coordinate $u$ on the target and a translated root coordinate $r$, the local equation has the form $(r^2-u)(r-r_0)=0$ with $r_0\neq0$.  A loop $u=\eps e^{2\pi i\theta}$ exchanges the two roots $r=\pm\sqrt u$ and fixes the third, so its monodromy is a transposition.  This is the usual covering-space monodromy of a simple branch point; compare \cite[Section 1.3]{Hatcher2002}.  A transitive subgroup of $S_3$ containing a transposition is all of $S_3$.
\end{proof}
```

---

## K07-06 — Euler characteristics of strata

**Status:** llm proved

**Claim.** χ(A^3\S)=0, χ(S\Γ)=1, χ(S)=1, and χ(T)=1.

**Direct dependencies:** `K07-02`, `K07-04`

**Manuscript label:** `cor:euler-strata`

### Current manuscript statement

```tex
\begin{corollary}\label{cor:euler-strata}
One has
\[
  \chi(\A^3\setminus\cS)=0,
  \qquad
  \chi(\cS\setminus\Gamma)=1,
  \qquad
  \chi(\cS)=1,
  \qquad
  \chi(\cT)=1.
\]
\end{corollary}
```

### Claimed proof

```tex
\begin{proof}
Using additivity and multiplicativity of $\chi$ as recalled in \cref{sec:background} and \cite[Chapter 4]{Dimca2004}, let $u=\chi(\A^3\setminus\cS)$ and $v=\chi(\cS\setminus\Gamma)$.  Since $\chi(\Gamma)=\chi(\C^*)=0$, the target stratification gives
\[
  u+v=1.
\]
The source stratification and \cref{prop:T-isomorphism} give
\[
  3u+v=1.
\]
Thus $u=0$ and $v=1$.
\end{proof}
```

---

## K07-07 — Affine elimination coordinate collision

**Status:** llm proved

**Claim.** The affine elimination cubic can identify distinct sheets at a specific target point; therefore the affine x-coordinate/resultant description is not a globally sheet-separating inverse parametrization.

**Direct dependencies:** `K06-02`

**Manuscript label:** `prop:affine-coordinate-collision`

### Current manuscript statement

```tex
\begin{proposition}[A coordinate collision in the elimination cubic]\label{prop:affine-coordinate-collision}
At
\[
  (A,B,C)=\left(-\frac8{27},0,1\right)
\]
the polynomial \eqref{eq:affine-elimination-cubic} is
\[
  -\frac2{27}(2x+3)(4x-3)^2,
\]
but the fiber of $F$ has three distinct points:
\[
\begin{aligned}
 p_1&=\left(-\frac32,\frac43,\frac{104}{27}\right),\\
 p_2&=\left(\frac34,-\frac23+\frac{2\sqrt3}{3},
                  \frac{104}{27}-\frac{8\sqrt3}{3}\right),\\
 p_3&=\left(\frac34,-\frac23-\frac{2\sqrt3}{3},
                  \frac{104}{27}+\frac{8\sqrt3}{3}\right).
\end{aligned}
\]
All three map to $(-8/27,0,1)$.  Thus the double affine root $x=3/4$ corresponds to two different fiber points.
\end{proposition}
```

### Claimed proof

```tex
\begin{proof}
Direct exact substitution gives the stated factorization and the three equal images.  Notice that
\[
  \Delta\left(-\frac8{27},0,1\right)=\frac{64}{27}\neq0,
\]
so the map is an honest three-sheeted etale covering near this target.  The squared factor in \eqref{eq:affine-cubic-discriminant} records a collision of the chosen coordinate $x$, not ramification of $F$.
\end{proof}
```

---

## K08-01 — Ordered double-point variety Y

**Status:** llm proved

**Claim.** Y is a smooth irreducible affine threefold, finite étale degree 6 over A^3\S, with χ(Y)=0 and a nonconstant discriminant unit; hence no affine cylinder over Y is affine space.

**Direct dependencies:** `K06-01`, `K07-02`, `K07-05`, `K07-06`

**Manuscript label:** `thm:Y`

### Current manuscript statement

```tex
\begin{theorem}\label{thm:Y}
The variety $Y$ is a smooth irreducible affine threefold.  The map to the common target is a finite etale cover
\[
  Y\longrightarrow\A^3\setminus\cS
\]
of degree six, and
\[
  \chi(Y)=0.
\]
Moreover, $\Delta\circ F$ is a nonconstant unit on $Y$.  Hence
\[
  Y\times\A^d\not\simeq\A^{3+d}
  \qquad(d\geq0).
\]
\end{theorem}
```

### Claimed proof

```tex
\begin{proof}
Since $F$ is etale, it is unramified and its diagonal in the fiber product is an open immersion \cite[Tag 02G3, Lemma 29.36.13]{StacksProject}; since $F$ is separated, the diagonal is a closed immersion \cite[Tag 01KK]{StacksProject}.  Therefore its complement is also open and closed in the affine variety $Z$, hence is affine.  The fiber census shows that no distinct pair lies over $\cS$, so $Y$ is the ordered-pair cover over $\A^3\setminus\cS$.  It is finite etale of degree $3\cdot2=6$, hence smooth.

The $S_3$ monodromy acts transitively on ordered pairs of distinct sheets.  Thus the cover is connected.  A connected smooth complex variety is irreducible: smoothness makes all local rings regular, hence domains, so distinct irreducible components cannot meet; compare \cite[Chapter I, Section 5]{Hartshorne1977}.  By \cref{cor:euler-strata},
\[
  \chi(Y)=6\chi(\A^3\setminus\cS)=0.
\]
The pullback $\Delta\circ F$ has empty zero locus on the affine variety $Y$, so the Nullstellensatz makes it a unit; it is nonconstant because $Y$ dominates $\A^3\setminus\cS$ \cite[Chapters 1 and 5]{AtiyahMacdonald}.  Units are unchanged by adjoining polynomial variables over a reduced ring, whereas affine space has only constant units; see \cite[Chapter 1]{AtiyahMacdonald}.
\end{proof}
```

---

## K08-02 — Discriminant filling X

**Status:** llm proved

**Claim.** X={w^2=δ(F)} is smooth and irreducible; its branch divisor is T≅S\Γ; X minus the branch divisor is Y up to deck involution; χ(X)=1.

**Direct dependencies:** `K06-01`, `K07-01`, `K07-02`, `K07-04`, `K07-05`, `K07-06`, `K08-01`

**Manuscript label:** `thm:filling`

### Current manuscript statement

```tex
\begin{theorem}\label{thm:filling}
The threefold $X$ is smooth and irreducible.  Its branch divisor
\[
  \cB=V_X(w)
\]
is isomorphic to $\cT\simeq\cS\setminus\Gamma$, and there is an isomorphism, unique up to the deck involution $w\mapsto-w$,
\[
  X\setminus\cB\simeq Y.
\]
Consequently,
\[
  \chi(X)=1.
\]
\end{theorem}
```

### Claimed proof

```tex
\begin{proof}
Let $G=w^2-\delta(F)$.  If a point of $X$ were singular, then $\partial G/\partial w=2w$ would force $w=0$, and the source derivatives would give
\[
  dF^{\mathsf T}\,d\delta=0,
\]
where $dF^{\mathsf T}$ is the transpose of the differential of $F$.
The matrix $dF$ is invertible, so $d\delta=0$ at the target point.  By \cref{prop:discriminant-geometry}, that target lies on $\Gamma$.  But \cref{thm:fiber-census} says that $F$ has no preimage over $\Gamma$, a contradiction.  Thus $X$ is smooth.

The divisor $w=0$ is exactly the graph of the zero square root over $\cT=F^{-1}(\cS)$, so $\cB\simeq\cT$.

Over $U=\A^3\setminus\cS$, the cover $F^{-1}(U)\to U$ has monodromy $S_3$.  Fixing the first sheet identifies the stabilizer with $S_2$.  The two choices of a second sheet form the nontrivial two-sheeted cover associated with the sign character of that stabilizer.  On the other hand, adjoining a square root of the cubic discriminant is the sign-character cover of the $S_3$-torsor; after pulling back to the first-sheet cover, it is the same nontrivial $S_2$-cover.  The algebraic Riemann existence theorem and the classification of finite etale covers by monodromy \cite[Expose XII]{SGA1} therefore give
\[
  Y\simeq X\setminus\cB.
\]
Since $Y$ is irreducible and dense, $X$ is irreducible.  Finally,
\[
  \chi(X)=\chi(Y)+\chi(\cB)=0+1=1
\]
by \cref{cor:euler-strata} and \cref{thm:Y}.
\end{proof}
```

---

## K09-01 — Affine-modification presentation

**Status:** llm proved

**Claim.** The exact identity δ∘F=-9μ^2+8g and the coordinate change V=3μ-iw transform X into the hypersurface hz=N, exhibiting X as an affine modification of A^3.

**Direct dependencies:** `K08-02`

**Manuscript label:** `con:affine-modification-presentation`

### Current manuscript statement

```tex
\begin{construction}[Affine-modification presentation]\label{con:affine-modification-presentation}
Define
\begin{equation}\label{eq:mu-g}
  \mu=xz(1+xy)+y(1+3xy),
  \qquad
  g=(3xy+2)z+9y^2.
\end{equation}
The identity
\begin{equation}\label{eq:delta-identity}
  \delta\circ F=-9\mu^2+8g
\end{equation}
holds.  Set $V=3\mu-iw$, equivalently $w=i(V-3\mu)$.  This triangular automorphism carries \eqref{eq:X-filling} to
\begin{equation}\label{eq:hzN}
  X'=\{hz=N\}\subseteq\A^4_{x,y,V,z},
\end{equation}
where
\begin{equation}\label{eq:hN}
\begin{aligned}
  h&=6x(1+xy)V-24xy-16,\\
  N&=V^2-6y(1+3xy)V+72y^2.
\end{aligned}
\end{equation}
Thus $X\simeq X'$.  Put
\[
  M=\A^3_{x,y,V},
  \qquad H=V_M(h),
  \qquad C_0=V_M(h,N).
\]
The projection $\sigma\colon X'\to M$ is the affine modification of $M$ along $H$ with center $C_0$, because
\[
  \OO(X')=\C[x,y,V]\left[\frac Nh\right]\subseteq\C[x,y,V,h^{-1}].
\]
Below we identify $X$ with $X'$ and suppress the prime when no confusion can arise.
\end{construction}
```

### Claimed proof / derivation

No immediately following `proof` environment was found. The construction/statement itself contains the derivation, or this is an aggregate theorem whose proof is only a pointer to component results.

---

## K09-02 — Modification divisor and center

**Status:** llm proved

**Claim.** H≅(C*)^2, the center C0≅C*, the exceptional divisor D≅C*×A^1, and V_X(h)=D as a reduced Cartier divisor with div_X(h)=D.

**Direct dependencies:** `K09-01`

**Manuscript label:** `prop:modification-geometry`

### Current manuscript statement

```tex
\begin{proposition}\label{prop:modification-geometry}
The divisor $H$ is isomorphic to $(\C^*)^2$.  In the coordinates
\[
  t=1+xy,
\]
an isomorphism is
\begin{equation}\label{eq:H-param}
  (x,t)\longmapsto
  \left(x,\frac{t-1}{x},\frac{4(3t-1)}{3xt}\right).
\end{equation}
Under this identification,
\[
  N|_H=\frac{16(3t+1)}{9t^2x^2}.
\]
Hence
\begin{equation}\label{eq:center}
  C_0=\left\{3xy+4=0,\quad V=\frac8x\right\}
  \simeq\C^*,
\end{equation}
and the exceptional divisor is
\[
  D=\sigma^{-1}(C_0)\simeq C_0\times\A^1_z.
\]
Moreover,
\[
  V_X(h)=D
\]
as a reduced Cartier divisor, so $\divisor_X(h)=D$.
\end{proposition}
```

### Claimed proof

```tex
\begin{proof}
The equation $h=0$ becomes
\[
  6xtV-24t+8=0.
\]
On $H$, neither $x$ nor $t$ can vanish: if $x=0$, then $t=1$ and the equation reads $-16=0$; if $t=0$, it reads $8=0$.  Solving for $y$ and $V$ gives \eqref{eq:H-param}, with regular inverse $(x,y,V)\mapsto(x,1+xy)$.

Substitution gives the displayed formula for $N|_H$.  Its zero set is $t=-1/3$, simple in the $t$-coordinate, which yields \eqref{eq:center}.  Over a point of $H\setminus C_0$, the equation $hz=N$ has no solution.  Over $C_0$ it imposes no condition on $z$, so $D\simeq C_0\times\A^1$.  Finally,
\[
  \OO(X')/(h)
  \simeq\C[x,y,V,z]/(h,N)
  \simeq\OO(C_0)[z]
\]
is reduced.  Thus the principal divisor defined by $h$ is exactly $D$ with multiplicity one.
\end{proof}
```

---

## K09-03 — Factoriality, Picard group, units, rationality

**Status:** llm proved

**Claim.** X is rational and smooth, Cl(X)=0, Pic(X)=0, O(X) is a UFD, and O(X)^*=C^*.

**Direct dependencies:** `K08-02`, `K09-02`

**Manuscript label:** `thm:factorial-units`

### Current manuscript statement

```tex
\begin{theorem}\label{thm:factorial-units}
The threefold $X$ is rational and smooth, and
\[
  \Cl(X)=0,
  \qquad
  \Pic(X)=0,
  \qquad
  \OO(X)\text{ is a UFD},
  \qquad
  \OO(X)^*=\C^*.
\]
\end{theorem}
```

### Claimed proof

```tex
\begin{proof}
Smoothness was proved in \cref{thm:filling}.  The modification map is birational, so $X$ is rational.

Put
\[
  U_0=X\setminus D\simeq M\setminus H.
\]
Its coordinate ring is the localization $\C[x,y,V,h^{-1}]$, a UFD.  The localization sequence for divisor class groups \cite[Chapter II, Proposition 6.5]{Hartshorne1977} shows that $\Cl(X)$ is generated by the class of the single irreducible divisor $D$.  But $D=\divisor_X(h)$ is principal, so $\Cl(X)=0$.  Since $X$ is smooth, $\Pic(X)=\Cl(X)$ and $\Cl(X)=0$ is equivalent to factoriality of its normal affine coordinate ring \cite[Chapter II, Section 6]{Hartshorne1977}.

Because $\C[x,y,V]$ is a UFD and $h$ is irreducible, localization gives
\[
  \OO(M\setminus H)^*=\C^*h^{\Z};
\]
this is the standard description of units in the localization of a UFD at one prime element \cite[Chapters 1 and 3]{AtiyahMacdonald}.
Let $u\in\OO(X)^*$.  Its restriction to $U_0$ is $ch^m$ for some $c\in\C^*$ and $m\in\Z$.  The divisor of this restriction extends to $mD$ on $X$, while a global unit has zero divisor.  Hence $m=0$ and $u=c$.
\end{proof}
```

---

## K09-04 — Hyperbolic Gm action and quotient

**Status:** llm proved

**Claim.** The displayed Gm action preserves X, is hyperbolic, has the origin as unique fixed point with tangent weights (1,-1,-1), and has the stated invariant-ring quotient surface.

**Direct dependencies:** `K09-01`

**Manuscript label:** `prop:torus-quotient`

### Current manuscript statement

```tex
\begin{proposition}\label{prop:torus-quotient}
The action \eqref{eq:torus-action} has the origin as its unique fixed point.  The tangent weights at that point are $(1,-1,-1)$.  Its invariant ring is
\begin{equation}\label{eq:quotient-ring}
  R=\C[s,p,q]/(qh_0-N_0),
\end{equation}
where
\begin{equation}\label{eq:invariants}
  s=xy,
  \qquad p=xV,
  \qquad q=x^2z,
\end{equation}
\begin{equation}\label{eq:h0N0}
\begin{aligned}
  h_0(s,p)&=6p(1+s)-24s-16,\\
  N_0(s,p)&=p^2-6sp-18s^2p+72s^2.
\end{aligned}
\end{equation}
Thus the affine quotient is the surface
\[
  \Sigma_1=\Spec R.
\]
Evaluation at $x=1$ identifies $\Sigma_1$ with the fiber $\{x=1\}\subseteq X$.
\end{proposition}
```

### Claimed proof

```tex
\begin{proof}
Every ambient coordinate has nonzero weight, so an ambient fixed point must be the origin, which lies on $X$.  The linear part of the equation $hz-N$ at the origin is $-16z$.  Hence the tangent space is spanned by $x,y,V$, with weights $(1,-1,-1)$.

In the ambient polynomial ring, a weight-zero monomial
\[
  x^ay^bV^cz^d
\]
satisfies $a=b+c+2d$ and equals $s^bp^cq^d$.  Thus the ambient invariant ring is $\C[s,p,q]$.  The defining equation has weight $-2$.  Every ambient monomial of weight $2$ is $x^2$ times a weight-zero monomial.  Therefore the degree-zero part of the principal ideal $(hz-N)$ is generated over $\C[s,p,q]$ by
\[
  x^2(hz-N)=qh_0-N_0.
\]
This proves \eqref{eq:quotient-ring}.  Setting $x=1$ sends $(s,p,q)$ to $(y,V,z)$ and turns the quotient relation into the fiber equation, giving the last assertion.
\end{proof}
```

---

## K09-05 — Nonzero Rees fibers have κ̄=0

**Status:** llm proved

**Claim.** For c≠0, Σ_c={x=c} is smooth, χ(Σ_c)=2, and κ̄(Σ_c)=0, with the stated blowup-minus-conic model. The corrected Jacobian determinant of (h_c,N_c) at the center is 96, so transversality holds.

**Direct dependencies:** `K09-01`, `K09-04`

**Manuscript label:** `prop:surface-kappa`

### Current manuscript statement

```tex
\begin{proposition}\label{prop:surface-kappa}
For every $c\neq0$, the surface $\Sigma_c$ is smooth and
\[
  \kbar(\Sigma_c)=0,
  \qquad
  \chi(\Sigma_c)=2.
\]
More precisely,
\[
  \Sigma_c\simeq\operatorname{Bl}_{P_c}(\A^2_{y,V})\setminus\widetilde H_c,
\]
where
\[
  H_c=\{6c(1+cy)V-24cy-16=0\}
\]
is a smooth affine conic, and
\[
  P_c=\left(-\frac4{3c},\frac8c\right)
\]
is a transverse center point on $H_c$.
\end{proposition}
```

### Claimed proof

```tex
\begin{proof}
The equation of $\Sigma_c$ is $h_cz=N_c$, an affine modification of $\A^2_{y,V}$ along $H_c=V(h_c)$ with center $V(h_c,N_c)$.  In homogeneous coordinates $[Y:V:W]$ on $\mathbb P^2$, the projective closure of $H_c$ has equation
\[
  6cVW+6c^2YV-24cYW-16W^2=0.
\]
The determinant of its symmetric coefficient matrix is $-72c^4\neq0$, so the conic is smooth.  On the line at infinity $W=0$ its equation is $6c^2YV=0$, giving the two distinct points $[1:0:0]$ and $[0:1:0]$; the linear terms in the corresponding affine charts show that both intersections are transverse.  Solving $h_c=N_c=0$ gives the single point $P_c$, and the Jacobian determinant of $(h_c,N_c)$ there is $-72c^2\neq0$, so the center is transverse.  The blowup description of an affine modification \cite[Section 1]{KalimanZaidenberg1999} gives
\[
  \Sigma_c\simeq\operatorname{Bl}_{P_c}(\A^2)\setminus\widetilde H_c.
\]

Compactify by $\overline\Sigma_c=\operatorname{Bl}_{P_c}(\mathbb P^2)$.  Let $H$ denote the pullback of a line and $E$ the exceptional curve.  The boundary is the union of the line at infinity, of class $H$, and the proper transform of the conic, of class $2H-E$.  It is a simple normal-crossings divisor, and
\[
  K_{\overline\Sigma_c}= -3H+E,
  \qquad
  D_{\infty}=H+(2H-E)=3H-E.
\]
The canonical-divisor formula for the blowup of a smooth surface at a point is the standard formula $K_{\operatorname{Bl}_P S}=\pi^*K_S+E$; see \cite[Chapter II, Exercise 8.5]{Hartshorne1977}.  Thus
\[
  K_{\overline\Sigma_c}+D_{\infty}=0,
\]
so all logarithmic plurigenera are one and $\kbar(\Sigma_c)=0$.

Finally, blowing up one point replaces a point of Euler characteristic one by an exceptional $\mathbb P^1$ of Euler characteristic two, so $\chi(\operatorname{Bl}_{P_c}\A^2)=2$.  The removed affine conic is the projective conic minus its two points at infinity, hence isomorphic to $\C^*$ and has Euler characteristic zero; these uses of additivity are covered by \cite[Chapter 4]{Dimca2004}.  Hence $\chi(\Sigma_c)=2$.
\end{proof}
```

---

## K10-01 — Weighted extended-Rees presentation

**Status:** llm proved

**Claim.** O(X) is the extended Rees algebra of the (1,1,2)-weighted-order filtration on the quotient surface, with the stated graded-piece identities.

**Direct dependencies:** `K09-04`

**Manuscript label:** `thm:explicit-rees`

### Current manuscript statement

```tex
\begin{theorem}[Rees presentation]\label{thm:explicit-rees}
For $n\geq0$ and $m\geq1$,
\[
  \cE_n=x^nR,
  \qquad
  x^m\cE_{-m}=\mathfrak a_m.
\]
Consequently,
\begin{equation}\label{eq:explicit-extended-rees}
  \cE\simeq\bigoplus_{n\in\Z}\mathfrak a_{-n}x^n
  \subseteq R[x,x^{-1}]
\end{equation}
is the extended Rees algebra of the weighted filtration \eqref{eq:weighted-ideal}.
\end{theorem}
```

### Claimed proof

```tex
\begin{proof}
The graded quotient $\cE$ is spanned in each degree by classes of ambient monomials.  Let
\[
  M=x^ay^bV^cz^d.
\]
If $\wt(M)=n\geq0$, then
\[
  a-b-c-2d=n,
\]
so
\[
  M=x^n s^bp^cq^d\in x^nR.
\]
The reverse inclusion is clear, proving $\cE_n=x^nR$.

If $\wt(M)=-m$, then
\[
  b+c+2d-a=m.
\]
Multiplying by $x^m$ gives
\[
  x^mM=s^bp^cq^d,
\]
whose weighted order is
\[
  b+c+2d=a+m\geq m.
\]
Thus $x^m\cE_{-m}\subseteq\mathfrak a_m$.

Conversely, let $s^bp^cq^d$ have weighted order
\[
  w=b+c+2d\geq m.
\]
Then
\[
  x^{w-m}y^bV^cz^d\in \cE_{-m}
\]
and multiplying by $x^m$ gives $s^bp^cq^d$.  Since such monomials generate $\mathfrak a_m$, equality follows.

Finally, after inverting $x$ one has
\[
  y=sx^{-1},\qquad V=px^{-1},\qquad z=qx^{-2},
\]
so $\cE[x^{-1}]=R[x,x^{-1}]$.  The graded-piece equalities therefore identify $\cE$ with the extended Rees subalgebra displayed in \eqref{eq:explicit-extended-rees}.
\end{proof}
```

---

## K10-02 — Rigidity of X

**Status:** llm proved

**Claim.** X is rigid: ML(X)=O(X). In particular X is not A^3 and X×A^1 is not A^4.

**Direct dependencies:** `K05-01`, `K09-05`, `K10-01`

**Manuscript label:** `cor:X-rigid`

### Current manuscript statement

```tex
\begin{corollary}[Rigidity of the filling]\label{cor:X-rigid}
The threefold $X$ is rigid:
\[
  \ML(X)=\OO(X).
\]
In particular,
\[
  X\not\simeq\A^3,
  \qquad
  X\times\A^1\not\simeq\A^4.
\]
\end{corollary}
```

### Claimed proof

```tex
\begin{proof}
By \cref{prop:surface-kappa}, the smooth affine surface $\Sigma_1$ has $\kbar(\Sigma_1)=0$.  By \cref{thm:explicit-rees}, $\OO(X)$ is its finitely generated extended Rees algebra.  Apply \cref{thm:rees-rigidity,cor:general-cancellation}.
\end{proof}
```

---

## K10-03 — Smooth Rees degeneration

**Status:** llm proved

**Claim.** x:X→A^1 is smooth and surjective; all nonzero fibers are mutually isomorphic with κ̄=0 and log plurigenera 1, while X_0≅A^2 has κ̄=-∞ and log plurigenera 0.

**Direct dependencies:** `K09-05`, `K10-01`

**Manuscript label:** `prop:smooth-family`

### Current manuscript statement

```tex
\begin{proposition}[Smooth Rees degeneration]\label{prop:smooth-family}
The coordinate
\[
  x\colon X\longrightarrow\A^1
\]
is a smooth surjective morphism.  Write $X_c=x^{-1}(c)$.  Its fibers satisfy
\[
  X_c\simeq\Sigma_1,
  \quad \kbar(X_c)=0
  \qquad(c\neq0),
\]
and
\[
  X_0\simeq\A^2,
  \quad \kbar(X_0)=-\infty.
\]
All logarithmic plurigenera $P_m^{\log}$ drop from $1$ on the nonzero fibers to $0$ on the special fiber.
\end{proposition}
```

### Claimed proof

```tex
\begin{proof}
By \cref{thm:explicit-rees}, $\C[x]$ embeds in $\cE$.  Since $\cE$ is a domain, it is torsion-free over the PID $\C[x]$, and therefore flat \cite[Tag 0AUW]{StacksProject}.  The nonzero fibers are mutually isomorphic by the $\Gm$-action.  At $x=0$, the equation $hz=N$ becomes
\[
  -16z=V^2-6yV+72y^2,
\]
which solves uniquely for $z$ and identifies the fiber with $\A^2_{y,V}$.  Every nonzero fiber is smooth by \cref{prop:surface-kappa}, the special fiber is smooth, and the total space is smooth.  A flat morphism locally of finite presentation with smooth geometric fibers is smooth \cite[Tag 01V4, Lemma 29.35.3]{StacksProject}.  For $c\neq0$, the equality $K_{\overline\Sigma_c}+D_{\infty}=0$ gives $P_m^{\log}(X_c)=h^0(\overline\Sigma_c,\OO)=1$.  For $\A^2$, use the completion $(\mathbb P^2,L_\infty)$, for which $K_{\mathbb P^2}+L_\infty=-2H$; hence $P_m^{\log}(\A^2)=0$ for every $m\geq1$ and $\kbar(\A^2)=-\infty$.  Related deformation phenomena for affine-line fibrations are discussed in \cite{GurjarMasudaMiyanishi2014}; the point here is the explicit equivariant Rees realization.
\end{proof}
```

---

## K11-01 — Simple connectivity

**Status:** llm proved

**Claim.** π1(X)=1.

**Direct dependencies:** `K09-02`

**Manuscript label:** `thm:pi1`

### Current manuscript statement

```tex
\begin{theorem}\label{thm:pi1}
The modification map induces
\[
  \pi_1(X)\simeq\pi_1(M)=1.
\]
\end{theorem}
```

### Claimed proof

```tex
\begin{proof}
Apply \cref{lem:meridian} to $H\subset M$ and $D\subset X$.  Both fundamental groups are quotients of $\pi_1(U_0)$ by the normal closure of a meridian.  On the common complement, the defining function is the same regular function $h$.  Since
\[
  \divisor_X(h)=D
\]
with multiplicity one, a small loop on which $h$ winds once around zero is simultaneously a meridian of $D$ and, under $\sigma$, a meridian of $H$.  The two normal subgroups coincide, so
\[
  \pi_1(X)\simeq\pi_1(M)=1.
\]
\end{proof}
```

---

## K11-02 — Integral homology and π2

**Status:** llm proved

**Claim.** H_i(X;Z)=Z for i=0,2,3 and 0 otherwise; π2(X)≅Z.

**Direct dependencies:** `K09-02`, `K11-01`

**Manuscript label:** `thm:homology`

### Current manuscript statement

```tex
\begin{theorem}\label{thm:homology}
The integral homology of $X$ is
\[
  H_i(X;\Z)=
  \begin{cases}
    \Z,&i=0,2,3,\\
    0,&\text{otherwise}.
  \end{cases}
\]
Moreover,
\[
  \pi_2(X)\simeq\Z.
\]
\end{theorem}
```

### Claimed proof

```tex
\begin{proof}
The Thom isomorphism for an oriented real rank-two normal bundle \cite[Chapter VI]{Bredon1993} gives
\begin{equation}\label{eq:thom-M}
  H_i(M,U_0;\Z)\simeq H_{i-2}(H;\Z),
\end{equation}
\begin{equation}\label{eq:thom-X}
  H_i(X,U_0;\Z)\simeq H_{i-2}(D;\Z).
\end{equation}
Since $M$ is contractible and $H\simeq(\C^*)^2$, the long exact sequence of $(M,U_0)$ gives
\begin{equation}\label{eq:U-homology}
  H_1(U_0)=\Z,
  \qquad
  H_2(U_0)=\Z^2,
  \qquad
  H_3(U_0)=\Z,
\end{equation}
with all higher reduced homology zero.

Naturality of the Thom isomorphism identifies the relative map induced by
\[
  \sigma\colon(X,U_0)\longrightarrow(M,U_0)
\]
with the map induced by $D\to H$.  Since $D\simeq C_0\simeq\C^*$ by deformation retraction, this is the inclusion \eqref{eq:center-inclusion}.  On $H_0$ it is an isomorphism, and on $H_1$ it is the primitive injection \eqref{eq:H1-inclusion}.

Therefore the boundary maps in the long exact sequence of $(X,U_0)$ are:
\[
  H_2(X,U_0)=\Z\xrightarrow{\sim}H_1(U_0)=\Z,
\]
\[
  H_3(X,U_0)=\Z\hookrightarrow H_2(U_0)=\Z^2,
  \qquad 1\longmapsto(1,0),
\]
and $H_4(X,U_0)=0$.  Exactness now gives
\[
  H_1(X)=0,
\]
\[
  H_2(X)\simeq\operatorname{coker}(\Z\hookrightarrow\Z^2)\simeq\Z,
\]
\[
  H_3(X)\simeq H_3(U_0)\simeq\Z,
\]
and no higher homology.  Together with \cref{thm:pi1}, the degree-two Hurewicz theorem identifies $\pi_2(X)$ with $H_2(X;\Z)$ \cite[Section 4.2]{Hatcher2002}.
\end{proof}
```

---

## K11-03 — Stable topological obstruction

**Status:** llm proved

**Claim.** X is noncontractible and X×A^d is not A^{3+d} for every d≥0.

**Direct dependencies:** `K11-02`

**Manuscript label:** `cor:all-cylinders`

### Current manuscript statement

```tex
\begin{corollary}[Stable topological obstruction]\label{cor:all-cylinders}
The threefold $X$ is not contractible.  For every $d\geq0$,
\[
  X\times\A^d\not\simeq\A^{3+d}.
\]
\end{corollary}
```

### Claimed proof

```tex
\begin{proof}
The projection $X\times\A^d\to X$ is a homotopy equivalence, whereas affine space is contractible.  Since $H_2(X;\Z)\simeq\Z$, no such isomorphism exists.
\end{proof}
```

---

## K12-01 — Reconstruction from active monodromy data

**Status:** llm proved

**Claim.** Étale-maximal Keller maps correspond to active monodromy data whose canonical étale filling is affine space; the Jacobian-conjecture counterexample condition is equivalent to existence of a nontrivial active datum whose étale filling contains a dense open A^n.

**Direct dependencies:** none

**Manuscript label:** `thm:active-reconstruction`

### Current manuscript statement

```tex
\begin{theorem}[Reconstruction from active data]\label{thm:active-reconstruction}
The following statements hold.

\begin{enumerate}[label=\textup{(\alph*)}]
\item Etale-maximal Keller maps of generic degree $d\geq2$, up to source and target automorphisms, correspond to active degree-$d$ monodromy data $(S,\rho)$ for which
\[
  \widehat Z(S,\rho)\simeq\A^n.
\]
\item The Jacobian conjecture in dimension $n$ is equivalent to the following statement: no nontrivial active datum $(S,\rho)$ has an etale filling $\widehat Z(S,\rho)$ containing a dense Zariski-open subset isomorphic to $\A^n$.
\end{enumerate}
\end{theorem}
```

### Claimed proof

```tex
\begin{proof}
For (a), suppose first that $(S,\rho)$ is active and that $\widehat Z(S,\rho)\simeq\A^n$.  Compose such an isomorphism with the finite map restricted to the etale locus:
\[
  G\colon\A^n\simeq\widehat Z(S,\rho)\longrightarrow\A^n.
\]
This is an etale morphism of affine spaces and therefore, by the affine algebra--geometry dictionary, a polynomial map.  Its Jacobian determinant is a unit of $\C[x_1,\ldots,x_n]$, hence a nonzero constant \cite[Chapter 1]{AtiyahMacdonald}.  Its function field has degree $d$, so $d\geq2$ makes it noninvertible.  It is etale-maximal by construction.  Activity ensures that the reconstructed branch hypersurface is exactly $S$.

Conversely, an etale-maximal Keller map determines the finite normalization \eqref{eq:ZMT}, its actual reduced branch locus $S$, and the monodromy representation over $\A^n\setminus S$.  Purity of the branch locus makes $S$ either empty or a hypersurface \cite[Tag 0BJE]{StacksProject}.  This datum is active, and etale-maximality identifies the source with $\widehat Z(S,\rho)$.  Source and target automorphisms have exactly the expected effect on the chosen affine-space identification and on the branch datum.

For (b), a Keller counterexample gives, by \eqref{eq:ZMT}, a dense open immersion
\[
  \A^n\hookrightarrow\widehat Z(S,\rho)
\]
for the active datum obtained from the actual branch locus, which is a hypersurface by purity \cite[Tag 0BJE]{StacksProject}.  Conversely, if an active datum has a dense open subset $V\simeq\A^n$ inside its etale locus, then the composite
\[
  \A^n\simeq V\hookrightarrow\widehat Z(S,\rho)\longrightarrow\A^n
\]
is an etale polynomial map of generic degree $d\geq2$, hence a Keller counterexample.
\end{proof}
```

---

## K12-02 — The explicit F is étale-maximal

**Status:** llm proved

**Claim.** The explicit map F is étale-maximal; its active finite normalization is the incidence variety and the source exhausts its étale locus.

**Direct dependencies:** `K06-02`, `K06-03`, `K12-01`

**Manuscript label:** `prop:F-etale-maximal`

### Current manuscript statement

```tex
\begin{proposition}\label{prop:F-etale-maximal}
The explicit map $F$ in \eqref{eq:F} is etale-maximal.  Its active finite normalization is the incidence variety
\[
  \pi\colon\cI\longrightarrow\A^3,
\]
and its etale locus is $\cI^{\mathrm{simp}}\simeq\A^3$.
\end{proposition}
```

### Claimed proof

```tex
\begin{proof}
By \cref{prop:incidence-finite}, $\cI$ is finite, smooth, normal, and irreducible.  By \cref{thm:incidence-isomorphism}, its dense etale locus is isomorphic to the source and therefore has the same function field.  Hence $\cI$ is the normalization of the target in that function field.  The etale locus of the finite projection is exactly the simple-root locus, again by \cref{prop:incidence-finite}, and its reduced branch locus is $\cS$.  Thus the datum is active and the source exhausts the etale locus.
\end{proof}
```

---

## K13-01 — No compatible additive one-parameter symmetries

**Status:** llm proved

**Claim.** There is no nontrivial pair of algebraic Ga-actions on source and target making F equivariant.

**Direct dependencies:** `K07-03`, `K10-02`

**Manuscript label:** `prop:no-additive-symmetries`

### Current manuscript statement

```tex
\begin{proposition}\label{prop:no-additive-symmetries}
There is no nontrivial pair of algebraic $\Ga$-actions $\alpha_t$ on the source and $\beta_t$ on the target satisfying
\[
  F\circ\alpha_t=\beta_t\circ F
  \qquad(t\in\Ga).
\]
\end{proposition}
```

### Claimed proof

```tex
\begin{proof}
Equivariance makes the target action preserve the intrinsic nonproperness hypersurface $\cS$.  Since $\Delta$ is irreducible, one has
\[
  \beta_t^*(\Delta)=\vartheta(t)\Delta
\]
for a character $\vartheta\colon\Ga\to\Gm$.  The additive group has no nontrivial algebraic characters \cite[Chapter 12]{Milne2017}, so $\vartheta=1$.  Hence $\delta\circ\beta_t=\delta$, and the source action lifts to
\[
  (p,w)\longmapsto(\alpha_t(p),w)
\]
on $X$.  By the LND--$\Ga$ correspondence \cite[Chapter 1]{Freudenburg2017} and \cref{cor:X-rigid}, every $\Ga$-action on $X$ is trivial.  The finite projection $X\to\A^3$ to the source is surjective because every complex number has a square root.  Therefore triviality of the lifted action implies that $\alpha_t$ is trivial.  Dominance of $F$ then forces $\beta_t$ to be trivial as well.
\end{proof}
```

---

## K13-02 — Cancellation candidates fail

**Status:** llm proved

**Claim.** Y fails as a cancellation candidate because of its nonconstant unit; X fails because it is rigid and, independently, noncontractible with stable nonzero homology.

**Direct dependencies:** `K08-01`, `K09-03`, `K10-02`, `K11-03`

**Manuscript label:** `prop:cancellation-failure-mechanisms`

### Current manuscript statement

```tex
\begin{proposition}[Complementary failure mechanisms]\label{prop:cancellation-failure-mechanisms}
The two natural cancellation candidates fail for complementary reasons.
\begin{enumerate}[label=\textup{(\arabic*)},leftmargin=2.5em]
\item The ordered double-point variety $Y$ is too open: the discriminant is a nonconstant unit, and this obstruction survives every affine cylinder.
\item The filling $X$ repairs the unit group and is factorial, but it is rigid and topologically nontrivial.  Its Makar--Limanov invariant is the whole coordinate ring and
\[
  H_2(X;\Z)\simeq H_3(X;\Z)\simeq\Z.
\]
Consequently no affine cylinder over $X$ is affine space.
\end{enumerate}
\end{proposition}
```

### Claimed proof

```tex
\begin{proof}
Statement (1) is \cref{thm:Y}.  Statement (2) combines \cref{thm:factorial-units,cor:X-rigid,thm:homology,cor:all-cylinders}.  The general theorem \cref{thm:rees-rigidity} shows that the rigidity mechanism is not peculiar to this Keller map.
\end{proof}
```

---

## K01-01 — Aggregate main package

**Status:** llm proved

**Claim.** The paper’s main package combines Rees rigidity; projective-cubic fiber geometry with 3/1/0 fibers, discriminant nonproperness and S3 monodromy; a smooth rational factorial rigid simply connected noncontractible X with χ=1, H2=H3=Z, π2=Z; and the smooth κ̄-dropping degeneration.

**Direct dependencies:** `K05-01`, `K07-02`, `K07-03`, `K07-05`, `K08-02`, `K09-03`, `K10-02`, `K10-03`, `K11-01`, `K11-02`, `K11-03`

**Manuscript label:** `thm:main-results`

### Current manuscript statement

```tex
\begin{theorem}[Main results, in geometric form]\label{thm:main-results}
The following statements hold.
\begin{enumerate}[label=\textup{(\roman*)},leftmargin=2.5em]
\item Let $\Sigma$ be a smooth affine surface with $\kbar(\Sigma)\geq0$.  Every finitely generated extended Rees algebra of a nonzero multiplicative ideal filtration on $\Sigma$ is rigid.
\item The fibers of $F$ are the simple projective roots of a binary cubic.  They have cardinality $3$, $1$, or $0$ according as the target point lies off the discriminant, on its smooth stratum, or on its triple-root curve.  The nonproperness locus is the discriminant hypersurface and the monodromy group is $S_3$.
\item The discriminant filling $X$ is smooth, rational, factorial, and rigid; $\OO(X)^*=\C^*$ and $\Pic(X)=0$.  Moreover,
\[
  \pi_1(X)=1,
  \qquad
  H_i(X;\Z)=
  \begin{cases}
    \Z,&i=0,2,3,\\
    0,&\text{otherwise},
  \end{cases}
  \qquad
  \pi_2(X)\simeq\Z.
\]
\item The Rees coordinate defines a smooth family $X\to\A^1$ whose nonzero fibers are mutually isomorphic surfaces of logarithmic Kodaira dimension zero and whose special fiber is $\A^2$.
\end{enumerate}
\end{theorem}
```

### Claimed proof

```tex
\begin{proof}[Location of the proofs]
Statement (i) is \cref{thm:rees-rigidity}.  Statement (ii) follows from \cref{thm:incidence-isomorphism,thm:fiber-census,thm:nonproperness,prop:monodromy}.  Statement (iii) is assembled from \cref{thm:filling,thm:factorial-units,cor:X-rigid,thm:pi1,thm:homology}.  Statement (iv) is \cref{prop:smooth-family}.
\end{proof}
```

---

## O-PRIORITY — Priority / novelty of the exact property package

**Status:** open

**Claim.** No previously published smooth complex affine threefold is known with the exact conjunction rational, factorial, rigid, simply connected, noncontractible, χ=1, and H2=H3=Z.

**Direct dependencies:** `K01-01`

**Claimed proof:** none; this remains open.

---

## O-AUT — Automorphism group and torus-action uniqueness

**Status:** open

**Claim.** Determine Aut(X); in particular decide whether the displayed Gm action is unique up to conjugacy or whether other torus actions exist.

**Direct dependencies:** `K09-04`, `K10-02`

**Claimed proof:** none; this remains open.

---

## O-FILT — Recovering the Rees filtration

**Status:** open

**Claim.** Determine how much of the multiplicative filtration can be recovered from the isomorphism type of the extended-Rees threefold, and which points/weights yield nonisomorphic rigid threefolds.

**Direct dependencies:** `K05-01`, `K10-01`

**Claimed proof:** none; this remains open.

---

## O-MONO — Effective monodromy recognition

**Status:** open

**Claim.** Make active-monodromy recognition effective in low degree; in dimension two, test whether surface classification excludes open affine-plane charts in the relevant canonical étale fillings.

**Direct dependencies:** `K12-01`, `K12-02`

**Claimed proof:** none; this remains open.

---
