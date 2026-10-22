       01 WS-I PIC 9(4).

       PROCEDURE DIVISION USING LINKED-ITEMS.
           MOVE L-L(1) TO RESULT
           PERFORM VARYING WS-I FROM 2 BY 1 UNTIL WS-I > 3
               IF L-L(WS-I) > RESULT
                   MOVE L-L(WS-I) TO RESULT
               END-IF
           END-PERFORM
           GOBACK.
       END PROGRAM MAX-ELEMENT.
