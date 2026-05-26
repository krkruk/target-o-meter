# Target-o-meter

A Web application that tracks one's shooting skills progress. A user takes a picture, the application
counts all holes in a paper target and stores the result. Over time, the application
shall generate a neat statistic and present the overall progress.


## MVP

* The user takes a picture of 2 standard paper targets:
  * 10m Air Pistol 170x170mm
  * 25m & 50 Precision Pistol Target 550x550mm
* The user chooses several parameters under which one can upload the pictures: 
  * gun category: pistol/rifle/shotgun/pcc
  * distance: 10, 15, 25, 50, 100 and manually entered value
  * caliber: .22LR, 9x19, .223, 7.62x39, slug (more to come)
* The application counts each hole and assign a corresponding value in points 0-10 and 'X' (direct center hit, also counts as 10)
* The application persists each result in a database for future reference
* The application displays the aggregated trend over time:
  * plot a diagram against the mentioned parameters
  * plot the diagram against the mentioned parameters and specific points (i.e., direct hit: 10 points)

## Non-functional

* The application uses a safe authentication mechanism: Google OAuth 2.0
* The application collects as few personal details as possible
* The application processes up to 3 images on the backend side at the same time

## Acceptance criteria

* The user can upload a picture of a paper target
* The application detects the holes with 90% fidelity rate
* The application plots trend lines
