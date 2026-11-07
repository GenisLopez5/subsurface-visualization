using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UIElements;
namespace Subsurface.Borehole
{
    public sealed class BoreholeCsvOverlay
    {
        readonly BoreholeViewer viewer;readonly VisualElement overlay,header;readonly ListView list;BoreholeDocument data;
        public BoreholeCsvOverlay(BoreholeViewer viewer,VisualElement root)
        {
            this.viewer=viewer;overlay=root.Q("csvOverlay");header=root.Q("csvHeader");list=root.Q<ListView>("csvRows");
            root.Q<Button>("toggleCsv").clicked+=()=>{overlay.ToggleInClassList("hidden");if(!overlay.ClassListContains("hidden")&&list.selectedIndex>=0)list.schedule.Execute(()=>list.ScrollToItem(list.selectedIndex));};root.Q<Button>("hideCsv").clicked+=()=>overlay.AddToClassList("hidden");
            overlay.RegisterCallback<WheelEvent>(e=>e.StopPropagation());overlay.RegisterCallback<PointerDownEvent>(e=>e.StopPropagation());
            root.Q<ScrollView>("csvHorizontal").contentContainer.style.flexDirection=FlexDirection.Column;header.style.flexShrink=0;list.style.flexShrink=0;
            list.fixedItemHeight=28;list.selectionType=SelectionType.Single;
            list.makeItem=()=>{var row=new VisualElement();row.AddToClassList("csv-row");for(int i=0;i<data.Headers.Length;i++){var cell=new Label();cell.enableRichText=false;cell.AddToClassList("csv-cell");row.Add(cell);}return row;};
            list.bindItem=(element,index)=>{var row=data.SourceRows[index];for(int i=0;i<data.Headers.Length;i++){var cell=(Label)element[i];cell.text=row.SourceValues[i];cell.tooltip=row.SourceValues[i];}};
            list.selectionChanged+=items=>{foreach(var item in items)if(item is BoreholeInterval row)viewer.SelectInterval(data.Intervals.IndexOf(row),true);};
            overlay.RegisterCallback<GeometryChangedEvent>(_=>list.style.height=Mathf.Max(40,overlay.contentRect.height-58));
        }
        public void Bind(BoreholeDocument document)
        {data=document;header.Clear();foreach(string name in data.Headers){var cell=new Label(name);cell.enableRichText=false;cell.AddToClassList("csv-cell");header.Add(cell);}float width=data.Headers.Length*145;header.style.width=width;list.style.width=width;list.itemsSource=data.SourceRows;list.Rebuild();}
        public void Reveal(int interval)
        {if(data==null)return;int index=data.SourceRows.IndexOf(data.Intervals[interval]);if(index<0)return;list.SetSelectionWithoutNotify(new[]{index});list.ScrollToItem(index);}
    }
}
