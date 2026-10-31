# A compact positivity certificate beyond sums of squares

All arithmetic in this task is exact. Construct one JSON certificate for the homogeneous ternary sextic F listed below. The objective is a **short algebraic positivity proof** and a simultaneous proof that F is not a sum of squares of real polynomials. A generic large sum-of-squares residual is not a qualifying short proof.

For any three real linear forms A, B, C, define

    T(A,B,C) = A^4 B^2 + A^2 B^4 + C^6 - 3 A^2 B^2 C^2.

This is nonnegative by the ordinary three-term arithmetic–geometric mean inequality. Find an exact identity

    F = sum_(i=1)^r a_i T(A_i,B_i,C_i) + sum_(j=1)^s b_j q_j^2,

where 0 <= r,s <= 3, all a_i,b_j are nonnegative rational numbers, A_i,B_i,C_i are rational linear forms in x,y,z, and q_j are rational homogeneous cubics. Zero weights and zero forms are allowed. The compactness bounds are part of the mathematical task, not a restriction on the method used to discover the certificate.

Also give 28 rational moments y_e, one for each degree-six exponent e in the order below. With v the ten cubic monomials in the specified order, form the 10 by 10 matrix H with H_ab = y_(a+b), where a and b are their exponent triples. Require H positive semidefinite and sum_e F_e y_e < 0. These conditions imply that F is not a sum of squares.

## The polynomial

F = sum_e F_e x^(e_1) y^(e_2) z^(e_3). The index column starts at zero. Every one of the 28 degree-six monomials is listed; a negative coefficient retains its sign.

| Index | Exponent (x,y,z) | F_e |
| --- | --- | --- |
| 0 | (6, 0, 0) | 586225 |
| 1 | (5, 1, 0) | 1213088 |
| 2 | (5, 0, 1) | -1992004 |
| 3 | (4, 2, 0) | -632306 |
| 4 | (4, 1, 1) | -2012588 |
| 5 | (4, 0, 2) | 2536216 |
| 6 | (3, 3, 0) | -1947584 |
| 7 | (3, 2, 1) | 4233880 |
| 8 | (3, 1, 2) | -69542 |
| 9 | (3, 0, 3) | -1462910 |
| 10 | (2, 4, 0) | 1799008 |
| 11 | (2, 3, 1) | 2020660 |
| 12 | (2, 2, 2) | -5024670 |
| 13 | (2, 1, 3) | 1423786 |
| 14 | (2, 0, 4) | 359623 |
| 15 | (1, 5, 0) | 4572752 |
| 16 | (1, 4, 1) | -7809808 |
| 17 | (1, 3, 2) | 3024152 |
| 18 | (1, 2, 3) | 988784 |
| 19 | (1, 1, 4) | -548770 |
| 20 | (1, 0, 5) | -29794 |
| 21 | (0, 6, 0) | 2125120 |
| 22 | (0, 5, 1) | -5792128 |
| 23 | (0, 4, 2) | 5973956 |
| 24 | (0, 3, 3) | -2756632 |
| 25 | (0, 2, 4) | 482903 |
| 26 | (0, 1, 5) | -7594 |
| 27 | (0, 0, 6) | 3373 |

The cubic coefficient order is:

    (3, 0, 0), (2, 1, 0), (2, 0, 1), (1, 2, 0), (1, 1, 1), (1, 0, 2), (0, 3, 0), (0, 2, 1), (0, 1, 2), (0, 0, 3)

Thus it is x^3, x^2 y, x^2 z, x y^2, x y z, x z^2, y^3, y^2 z, y z^2, z^3. Each linear-form row is its x,y,z coefficient vector. The moments use exactly the degree-six table order.

## Answer format and bounds

Return only one UTF-8 JSON object with exactly these three keys:

```json
{
  "blocks": [{"weight": "a", "forms": [["Ax","Ay","Az"],["Bx","By","Bz"],["Cx","Cy","Cz"]]}],
  "squares": [{"weight": "b", "coefficients": ["q0","q1","q2","q3","q4","q5","q6","q7","q8","q9"]}],
  "moments": ["y0", "...", "y27"]
}
```

The quoted symbolic names above are placeholders. Replace each scalar by a JSON integer or a rational string matching `[+-]?[0-9]+(?:/[0-9]+)?`, with nonzero denominator. Fractions need not be written in reduced form. After reduction, every numerator has absolute value at most 2^48 and every positive denominator is at most 2^48. JSON booleans, decimal numbers, NaN, duplicate keys, extra keys, and comments are invalid. The whole answer must occupy at most 32768 bytes. No additional conditions are imposed on the forms, supports, signs of their coefficients, normalization, or moment rank.

The attached standard-library verifier checks the identity, rational bounds, the two compactness limits, exact positive semidefiniteness, and strict negativity. Run it with `python verifier.py answer.json`. Discovery may use any mathematical reasoning or computation permitted by the challenge session. No numerical tolerance is used for acceptance.
