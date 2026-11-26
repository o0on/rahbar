# Export A3-Report-Template.docx to PDF with Word and report the page counts.
# The main report must stay <= 8 pages; the screenshot appendix does not count.
param(
    [string]$Docx = (Join-Path $PSScriptRoot 'A3-Report-Template.docx'),
    [string]$Pdf = (Join-Path $PSScriptRoot 'A3-Report.pdf')
)

$word = New-Object -ComObject Word.Application
$word.Visible = $false
try {
    $doc = $word.Documents.Open($Docx, $false, $true)
    $doc.Repaginate()
    $appendixStart = 0
    foreach ($p in $doc.Paragraphs) {
        if ($p.Range.Text.StartsWith('Appendix')) { $appendixStart = $p.Range.Start; break }
    }
    $mainPages = if ($appendixStart -gt 0) { $doc.Range(0, $appendixStart).ComputeStatistics(2) } else { $doc.ComputeStatistics(2) }
    Write-Output "Main report pages: $mainPages (limit 8)"
    Write-Output "Total pages including appendix: $($doc.ComputeStatistics(2))"
    $doc.SaveAs2($Pdf, 17)
    $doc.Close($false)
    Write-Output "Wrote $Pdf"
} finally {
    $word.Quit()
}
