       01 WS-I PIC 9(4).
       01 WS-J PIC 9(4).
       01 WS-DIST COMP-2.

       PROCEDURE DIVISION USING LINKED-ITEMS.
           MOVE 0 TO RESULT
           PERFORM VARYING WS-I FROM 1 BY 1 UNTIL WS-I > 5
               PERFORM VARYING WS-J FROM 1 BY 1 UNTIL WS-J > 5
                   IF WS-I NOT = WS-J
                       COMPUTE WS-DIST = L-NUMBERS(WS-I) - L-NUMBERS(WS-J)
                       IF WS-DIST < 0
                           COMPUTE WS-DIST = 0 - WS-DIST
                       END-IF
                       IF WS-DIST < L-THRESHOLD
                           MOVE 1 TO RESULT
                       END-IF
                   END-IF
               END-PERFORM
           END-PERFORM
           GOBACK.
       END PROGRAM HAS-CLOSE-ELEMENTS.
