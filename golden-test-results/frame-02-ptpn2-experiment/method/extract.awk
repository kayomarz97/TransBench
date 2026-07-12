BEGIN {
  while ((getline line < COLMAP) > 0) { split(line, a, " "); C[a[1]] = a[2] }
  close(COLMAP); nnz = 0
}
/^%/ { next }               # comment lines
!seen { seen = 1; next }    # first non-comment line = dimensions header -> skip
{
  c = $2
  nc = C[c]
  if (nc == "") next        # column not an NTC cell
  r = $1
  if (r <= 36601) { print r" "nc" "$3 > GEXBODY; nnz++ }
  else if (r >= 36804 && r <= 36807) { print (r-36803)" "nc" "$3 > HTOBODY }
}
END { print nnz > NNZFILE }
