using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEngine.UIElements;

namespace Subsurface.Lithology
{
    [RequireComponent(typeof(UIDocument))]
    public class LithologyDemoController : MonoBehaviour
    {
        public LithologyCatalog catalog;
        public LithologyRenderer specimen;
        public LithologyRenderer[] displays;
        public Camera viewCamera;
        public Transform movableSpecimen;
        public StyleSheet styleSheet;
        public int advancingFrames;
        public float elapsedTime;
        Label info;
        DropdownField labels;
        List<LithologyDefinition> filtered;
        Vector3 movableRest;
        DisplayMode mode;
        readonly List<Behaviour> mutedExternal = new List<Behaviour>();
        void OnEnable()
        {
            if(catalog==null||specimen==null)return;
            // Additive demo owns only temporary visibility changes, restored on disable/exit.
            foreach(var cam in Object.FindObjectsByType<Camera>(FindObjectsSortMode.None))if(cam.gameObject.scene!=gameObject.scene&&cam.enabled){mutedExternal.Add(cam);cam.enabled=false;}
            foreach(var light in Object.FindObjectsByType<Light>(FindObjectsSortMode.None))if(light.gameObject.scene!=gameObject.scene&&light.enabled){mutedExternal.Add(light);light.enabled=false;}
            var root=GetComponent<UIDocument>().rootVisualElement;root.Clear();
            if(styleSheet!=null)root.styleSheets.Add(styleSheet);
            root.pickingMode=PickingMode.Ignore;
            var panel=new ScrollView();panel.AddToClassList("control-panel");root.Add(panel);
            var eyebrow=new Label("SUBSURFACE  /  MATERIAL LAB");eyebrow.AddToClassList("eyebrow");panel.Add(eyebrow);
            var title=new Label("Lithology atlas");title.AddToClassList("title");panel.Add(title);
            panel.Add(new Label("180 identities · 25 shared families"));
            var note=new Label("ILLUSTRATIVE PROCEDURAL ASSETS\nColors, scales and coverage are display choices.");note.AddToClassList("notice");panel.Add(note);
            var views=new VisualElement();views.AddToClassList("button-row");
            views.Add(new Button(()=>Frame(0)){text="Families"});views.Add(new Button(()=>Frame(32)){text="Recipes"});views.Add(new Button(()=>Frame(64)){text="Cutaway"});panel.Add(views);
            panel.Add(new Button(FocusSpecimen){text="Inspect selected specimen"});
            var legend=new Toggle("Legend / pattern view");legend.RegisterValueChangedCallback(e=>{mode=e.newValue?DisplayMode.Legend:DisplayMode.Texture;foreach(var d in displays)if(d!=null)d.SetDisplayMode(mode);specimen.SetDisplayMode(mode);});panel.Add(legend);
            var search=new TextField("Find source label");panel.Add(search);
            filtered=catalog.definitions.ToList();labels=new DropdownField("Selected identity",filtered.Select(d=>d.rawLabel).ToList(),0);panel.Add(labels);
            search.RegisterValueChangedCallback(e=>{filtered=catalog.definitions.Where(d=>d.rawLabel.IndexOf(e.newValue,System.StringComparison.OrdinalIgnoreCase)>=0).ToList();labels.choices=filtered.Select(d=>d.rawLabel).ToList();if(filtered.Count>0){labels.index=0;Select(filtered[0]);}});
            labels.RegisterValueChangedCallback(e=>{var d=filtered.FirstOrDefault(x=>x.rawLabel==e.newValue);if(d!=null)Select(d);});
            var grain=new Slider("Grain scale multiplier",.25f,4){value=1};grain.RegisterValueChangedCallback(e=>{specimen.visual.grainMultiplier=e.newValue;specimen.Apply();});panel.Add(grain);
            var ex=new Slider("Schematic exaggeration",.125f,4){value=1};ex.RegisterValueChangedCallback(e=>{specimen.visual.exaggerationMultiplier=e.newValue;specimen.Apply();});panel.Add(ex);
            var relief=new Slider("Normal / relief strength",0,2){value=1};relief.RegisterValueChangedCallback(e=>{specimen.visual.normalMultiplier=e.newValue;specimen.Apply();});panel.Add(relief);
            var optional=new Toggle("Enable optional shaly lamination");optional.RegisterValueChangedCallback(e=>{specimen.visual.optionalLamination=e.newValue;specimen.Apply();ShowInfo();});panel.Add(optional);
            var move=new Slider("Move object-space specimen",-2,2);if(movableSpecimen!=null)movableRest=movableSpecimen.position;
            move.RegisterValueChangedCallback(e=>{if(movableSpecimen!=null)movableSpecimen.position=movableRest+Vector3.right*e.newValue;});panel.Add(move);
            info=new Label();info.AddToClassList("info");panel.Add(info);
            panel.Add(new Label("Source-specific codes remain undecoded.\nNo Sample retains solid interval geometry.\nPyrite preview uses an explicit sandstone host."));
            var initial=catalog.Resolve("Fine Sandstone").definition;labels.SetValueWithoutNotify(initial.rawLabel);Select(initial);Frame(0);
        }
        void Update(){advancingFrames++;elapsedTime+=Time.unscaledDeltaTime;}
        void OnDisable(){foreach(var b in mutedExternal)if(b!=null)b.enabled=true;mutedExternal.Clear();}
        void Select(LithologyDefinition d){specimen.SetDefinition(d);ShowInfo();}
        void ShowInfo()
        {
            if(info==null||specimen.definition==null)return;var d=specimen.definition;var b=d.EffectiveRecipe.baseTexture;
            info.text=d.stableId+"  /  "+d.rawLabel+"\n"+d.role+" · "+d.interpretation+"\nBase: "+(b!=null?b.stableId+" · "+b.description:"none selected")+"\nAsset: "+(b==null?"schematic / procedural":b.illustrativePreview?"illustrative preview":"user-supplied reference")+"\n\n"+d.sourceInstructions+"\n\n"+specimen.diagnostics;
        }
        public void Frame(float x)
        {
            if(viewCamera==null)return;viewCamera.transform.position=new Vector3(x,30,-8);viewCamera.transform.LookAt(new Vector3(x,0,4));viewCamera.orthographicSize=8.4f;
            if(x>60){viewCamera.transform.position=new Vector3(x+8,16,-12);viewCamera.transform.LookAt(new Vector3(x,0,4));viewCamera.orthographicSize=8.5f;}
        }
        public void FocusSpecimen(){if(viewCamera==null)return;viewCamera.transform.position=new Vector3(0,1.5f,-4.34f);viewCamera.transform.LookAt(new Vector3(0,1,-3.6f));viewCamera.orthographicSize=.24f;}
    }
}
