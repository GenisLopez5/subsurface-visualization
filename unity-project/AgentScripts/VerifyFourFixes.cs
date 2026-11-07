using System;using System.Linq;using System.IO;using System.Threading.Tasks;using UnityEngine;using UnityEngine.UIElements;using Subsurface.Borehole;using Subsurface.Lithology;
public static class VerifyFourFixes
{
 static BoreholeViewer V=>UnityEngine.Object.FindAnyObjectByType<BoreholeViewer>();
 static void Check(bool ok,string why){if(!ok)throw new Exception(why);}
 static void Click(Button button)=>typeof(Clickable).GetMethod("SimulateSingleClick",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic).Invoke(button.clickable,new object[]{null,0});
 public static object Audit()
 {
  var c=UnityEditor.AssetDatabase.LoadAssetAtPath<LithologyCatalog>("Assets/Lithology/Generated/LithologyCatalog.asset");var d=BoreholeCsv.Parse(File.ReadAllText("Assets/Borehole/Samples/Allegheny.csv"));
  var result=d.SourceRows.GroupBy(r=>r.Lithology).Select(g=>{var r=c.Resolve(g.Key);var compiled=LithologyRecipeCompiler.Compile(c,r.definition);return new{label=g.Key,intervals=g.Count(),lookup=r.status.ToString(),role=r.definition!=null?r.definition.role.ToString():"Unknown",schematic=compiled.schematic,definition=r.definition!=null?r.definition.stableId:null};}).ToArray();
  Directory.CreateDirectory("Assets/Borehole/Generated/Verification/Fixes");File.WriteAllText("Assets/Borehole/Generated/Verification/Fixes/MaterialAudit.json",Newtonsoft.Json.JsonConvert.SerializeObject(result,Newtonsoft.Json.Formatting.Indented));return result;
 }
 public static async Task<object> Run()
 {
  var v=V;int frame=v.AdvancingFrames;await Task.Delay(100);Check(v.AdvancingFrames>frame,"Frozen player");
  var unknown=v.column.Document.Intervals.Select((r,i)=>new{r,i}).Where(x=>!x.r.IsGap&&!v.column.catalog.Resolve(x.r.Lithology).IsResolved).ToArray();Check(unknown.Length==94,"Unexpected unknown count");Check(unknown.All(x=>v.column.Surfaces[x.i].diagnostics.Contains("Unrecognized")||v.column.Surfaces[x.i].diagnostics.Contains("unknown")),"Missing fallback diagnostic");
  string csv="top_depth,bottom_depth,lithology\n";for(int i=0;i<8;i++)csv+=(1000+i*10)+","+(1010+i*10)+","+(i%2==0?"Shale":"Sandstone")+"\n";Check(v.LoadCsvText(csv,"Label spacing fixture"),"Import failed");
  var overlay=v.Root.Q("csvOverlay");overlay.AddToClassList("hidden");v.SelectInterval(0,false);Check(overlay.ClassListContains("hidden"),"Selection opened CSV");
  Click(v.Root.Q<Button>("toggleCsv"));Check(!overlay.ClassListContains("hidden"),"CSV manual open failed");v.SelectInterval(1,false);Check(!overlay.ClassListContains("hidden")&&v.Root.Q<ListView>("csvRows").selectedIndex==1,"Open CSV selection not synced");Click(v.Root.Q<Button>("hideCsv"));v.SelectInterval(2,false);Check(overlay.ClassListContains("hidden"),"Selection reopened CSV");
  v.FitColumn();await Task.Delay(100);Check(v.Root.Query<Button>(className:"interval-label").ToList().All(x=>x.resolvedStyle.display==DisplayStyle.None),"Overview labels not hidden");Check(!v.Root.Q("labelHint").ClassListContains("hidden"),"No zoom hint");
  v.Zoom(.25);v.SetDepth(1035);await Task.Delay(100);var labels=v.Root.Query<Button>(className:"interval-label").ToList().Where(x=>x.resolvedStyle.display==DisplayStyle.Flex).ToArray();Check(labels.Length>=2&&labels[0].layout.x<labels[1].layout.x,"Labels do not alternate");
  var ground=v.GetComponent<BoreholeCutaway>();Check(ground.SurfaceOffset==3,"Missing compressed break");Check(v.column.Surfaces[0].transform.localPosition.y==0,"Logged cylinder geometry shifted");v.SetDepth(1000);await Task.Delay(100);Check(v.ShowingSurface&&!v.Root.Q("surfaceGap").ClassListContains("hidden"),"Surface break not visible");Check(v.Root.Q<Label>("surfaceGap").text.Contains("1,000"),"Wrong break depth");
  v.LoadCsvText("top_depth,bottom_depth,lithology\n0,10,Shale","Zero start");await Task.Delay(100);Check(ground.SurfaceOffset==0&&v.Root.Q("surfaceGap").ClassListContains("hidden"),"Invented break at zero");
  v.LoadCsvText("top_depth,bottom_depth,lithology\n1000000,1000010,Shale","Deep start");await Task.Delay(100);Check(ground.SurfaceOffset==3,"Depth break is physically scaled");
  v.SetAnalysisResult(v.initialCsv.text,v.initialDocumentImage,v.initialSourceName);v.SetDepth(v.column.Document.TopFeet);await Task.Delay(100);
  var result=new{passed=true,frames=v.AdvancingFrames-frame,csvStaysClosed=true,manualCsvStillSynchronizes=true,overviewHidesAllLabels=true,labelsAlternate=true,compressedGap=true,zeroHasNoGap=true,largeDepthSameGap=true,unknownIntervals=94};File.WriteAllText("Assets/Borehole/Generated/Verification/Fixes/LiveResults.json",Newtonsoft.Json.JsonConvert.SerializeObject(result));return result;
 }
}
