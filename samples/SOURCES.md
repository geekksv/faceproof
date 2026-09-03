# Sample images

All three are photographs of public figures from Wikimedia Commons, used here
as pipeline inputs and as the negative control in the test suite.

| file | subject | source |
|---|---|---|
| `virat_kohli.jpg` | Virat Kohli | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Virat_Kohli_during_the_India_vs_Aus_4th_Test_match_at_Narendra_Modi_Stadium_on_09_March_2023.jpg) |
| `ms_dhoni.jpg` | MS Dhoni | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:MS_Dhoni_(Prabhav_%2723_-_RiGI_2023).jpg) |
| `sundar_pichai.jpg` | Sundar Pichai | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Sundar_Pichai_-_2023_(cropped).jpg) |

Each is available under its own free licence on Commons; follow the links for the
exact terms and attribution. `ms_dhoni.jpg` and `sundar_pichai.jpg` exist so the
test suite can assert that unrelated faces score far below the match threshold.

`kl_rahul.jpg` was added later as a second end-to-end demo subject:
[KL Rahul 2022](https://commons.wikimedia.org/wiki/File:KL_Rahul_2022.jpg) —
it matches his official X profile at 0.527 against a *different* photograph,
which exercises real face recognition rather than duplicate-image matching.
